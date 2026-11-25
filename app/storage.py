"""Save articles as de-duplicated stories and read them back, on SQLite or DynamoDB.

    with storage.connect() as store:
        store.save_articles(articles)
        store.get_stories(since=...)

The backend is picked by the STORAGE_BACKEND environment variable ("sqlite" by
default, "dynamodb" on AWS). The matching logic lives here, once; each backend in
storage_sqlite.py / storage_dynamodb.py only reads and writes rows.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from dedup import MAX_GAP, normalise_link, same_story
from models import Article, Story

# Local database file at the project root (outside app/, so it is never packaged
# into the Lambda); override with the NEWS_DB environment variable.
DEFAULT_DB = Path(__file__).resolve().parent.parent / "news.db"


@dataclass(frozen=True)
class SaveResult:
    """What one save_articles() call changed."""

    new_articles: int  # articles added (to new or existing stories)
    new_stories: int   # stories created
    skipped: int       # articles already stored, ignored


class Backend(Protocol):
    """The row-level operations each storage backend provides."""

    def load_articles_since(self, since: datetime, until: datetime) -> list[tuple[str, Article]]:
        """Stored articles published in [since, until], each with its story id."""

    def add_article(self, story_id: str, article: Article, link_key: str) -> bool:
        """Store one article under a story. Returns False if (source, link_key) already exists."""

    def create_story(self, story_id: str, article: Article, now: datetime) -> None:
        """Create a story whose first (and only) source is `article`."""

    def update_story(self, story_id: str, article: Article, source_count: int) -> None:
        """Add `article` as another source: append (source, link), set source_count,
        and use its summary if the story has none yet."""

    def get_stories(self, since: datetime, until: datetime, limit: int) -> list[Story]:
        """Stories first seen in [since, until]: most sources first, then newest."""


def new_story_id(now: datetime) -> str:
    """Unique, roughly time-ordered id, e.g. "20261125120000-1a2b3c4d".

    It starts with the first_seen time, so DynamoDB can find a story's day partition
    from its id alone.
    """
    return f"{now.astimezone(UTC):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"


def _find_story(article: Article, candidates: list[tuple[str, Article]]) -> str | None:
    """Return the id of the stored story this article belongs to, or None.

    If it matches articles from several stories, pick the story with the most
    matching articles (ties: the oldest story id).
    """
    votes: dict[str, int] = {}
    for story_id, stored in candidates:
        if same_story(article, stored):
            votes[story_id] = votes.get(story_id, 0) + 1
    if not votes:
        return None
    # Most votes wins; on a tie, the smallest id (ids start with a timestamp: oldest story).
    best = max(votes.values())
    return min(sid for sid, count in votes.items() if count == best)


class Store:
    """The storage interface the rest of the app uses, on top of any Backend."""

    def __init__(self, backend: Backend) -> None:
        self.backend = backend

    def save_articles(self, articles: list[Article], now: datetime | None = None) -> SaveResult:
        """Add articles, attaching each to an existing story or a new one.

        Idempotent: an article this source already gave us (same normalised link)
        is skipped, so re-running the fetcher never creates duplicates.
        `now` (default: current UTC time) becomes first_seen for new stories.
        """
        now = now or datetime.now(UTC)
        if not articles:
            return SaveResult(0, 0, 0)

        # Oldest first, so a story's first article sets its title and summary.
        articles = sorted(articles, key=lambda a: a.published)

        # Load every stored article that could match an incoming one (within MAX_GAP).
        until = max(now, articles[-1].published)
        candidates = self.backend.load_articles_since(articles[0].published - MAX_GAP, until)

        # Build in-memory indexes: what's already stored, and which sources each story has.
        stored_keys = {(a.source, normalise_link(a.link)) for _, a in candidates}
        story_sources: dict[str, set[str]] = {}
        for story_id, stored in candidates:
            story_sources.setdefault(story_id, set()).add(stored.source)

        new_articles = new_stories = skipped = 0
        for article in articles:
            link_key = normalise_link(article.link)

            # 1. This source already gave us this link (e.g. the fetcher ran again): skip.
            if (article.source, link_key) in stored_keys:
                skipped += 1
                continue

            # 2. Find the story it belongs to, or prepare a new story id.
            story_id = _find_story(article, candidates)
            is_new = story_id is None
            if is_new:
                story_id = new_story_id(now)

            # 3. Store the article. The backend refuses duplicates as a safety net.
            if not self.backend.add_article(story_id, article, link_key):
                skipped += 1
                continue

            # 4. Create the story, or add this source to the existing one.
            sources = story_sources.setdefault(story_id, set())
            sources.add(article.source)
            if is_new:
                self.backend.create_story(story_id, article, now)
                new_stories += 1
            else:
                self.backend.update_story(story_id, article, len(sources))

            # 5. Later articles in this same batch can match this one too.
            candidates.append((story_id, article))
            stored_keys.add((article.source, link_key))
            new_articles += 1

        return SaveResult(new_articles, new_stories, skipped)

    def get_stories(self, since: datetime, until: datetime | None = None,
                    limit: int = 50) -> list[Story]:
        """Stories first seen between `since` and `until` (default: now), each with its
        (source, link) list. Ordered by source_count, then newest first."""
        return self.backend.get_stories(since, until or datetime.now(UTC), limit)


@contextmanager
def connect(path: Path | str | None = None, backend: str | None = None) -> Iterator[Store]:
    """Open storage. Backend: `backend` argument, else STORAGE_BACKEND, else "sqlite".

    SQLite uses `path`, else NEWS_DB, else news.db at the project root.
    DynamoDB uses the table named in TABLE_NAME.
    """
    name = backend or os.environ.get("STORAGE_BACKEND", "sqlite")

    if name == "sqlite":
        # Imported here so the Lambda never needs the SQLite module, and vice versa.
        import storage_sqlite

        db_path = path or os.environ.get("NEWS_DB") or DEFAULT_DB
        with storage_sqlite.SqliteBackend.open(db_path) as sqlite_backend:
            yield Store(sqlite_backend)
    elif name == "dynamodb":
        import storage_dynamodb

        yield Store(storage_dynamodb.DynamoBackend(os.environ["TABLE_NAME"]))
    else:
        raise ValueError(f"Unknown STORAGE_BACKEND {name!r}: use 'sqlite' or 'dynamodb'")
