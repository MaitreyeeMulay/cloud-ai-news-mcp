"""Save articles to SQLite as de-duplicated stories, and read stories back.

Two tables:
  stories        one row per story (title, summary, category, first_seen, source_count)
  story_sources  one row per article that reported it (source, link, ...)
(source, normalised link) is the primary key of story_sources, so saving
the same article twice is a no-op. Re-running the fetcher is safe.
"""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dedup import MAX_GAP, normalise_link, same_story
from models import Article, Story

# Database file; override with the NEWS_DB environment variable.
DEFAULT_DB = Path(os.environ.get("NEWS_DB", Path(__file__).with_name("news.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    summary      TEXT    NOT NULL DEFAULT '',
    category     TEXT    NOT NULL,
    first_seen   TEXT    NOT NULL,           -- ISO 8601 UTC: when we first saved it
    source_count INTEGER NOT NULL DEFAULT 0  -- distinct sources in story_sources
);
CREATE TABLE IF NOT EXISTS story_sources (
    source    TEXT NOT NULL,
    link_key  TEXT NOT NULL,                 -- normalise_link(link)
    story_id  INTEGER NOT NULL REFERENCES stories(id),
    link      TEXT NOT NULL,
    title     TEXT NOT NULL,                 -- the article's own headline (used for matching)
    category  TEXT NOT NULL,
    published TEXT NOT NULL,                 -- ISO 8601 UTC
    -- One row per (source, link): blocks re-saving an article, but still lets two
    -- sources point at the same page (e.g. Hacker News linking to an OpenAI post).
    PRIMARY KEY (source, link_key)
);
CREATE INDEX IF NOT EXISTS idx_sources_story     ON story_sources(story_id);
CREATE INDEX IF NOT EXISTS idx_sources_published ON story_sources(published);
"""


@dataclass(frozen=True)
class SaveResult:
    """What one save_articles() call changed."""

    new_articles: int  # articles added (to new or existing stories)
    new_stories: int   # stories created
    skipped: int       # articles already stored, ignored


def _iso(dt: datetime) -> str:
    """Store datetimes as UTC ISO strings, which also sort correctly as text."""
    return dt.astimezone(UTC).isoformat()


@contextmanager
def connect(path: Path | str = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    """Open the database (creating tables if needed); commit and close on exit."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _stored_articles_since(conn: sqlite3.Connection, since: datetime) -> list[tuple[int, Article]]:
    """Load stored articles published after `since`, with the story each belongs to."""
    rows = conn.execute(
        "SELECT story_id, source, link, title, category, published FROM story_sources "
        "WHERE published >= ?",
        (_iso(since),),
    ).fetchall()
    return [
        (row["story_id"], Article(title=row["title"], link=row["link"], source=row["source"],
                                  category=row["category"],
                                  published=datetime.fromisoformat(row["published"])))
        for row in rows
    ]


def _find_story(article: Article, candidates: list[tuple[int, Article]]) -> int | None:
    """Return the id of the stored story this article belongs to, or None.

    If it matches articles from several stories, pick the story with the most
    matching articles (ties: the oldest story).
    """
    votes: dict[int, int] = {}
    for story_id, stored in candidates:
        if same_story(article, stored):
            votes[story_id] = votes.get(story_id, 0) + 1
    if not votes:
        return None
    return max(votes, key=lambda sid: (votes[sid], -sid))


def save_articles(conn: sqlite3.Connection, articles: list[Article],
                  now: datetime | None = None) -> SaveResult:
    """Add articles to the database, attaching each to an existing story or a new one.

    Idempotent: articles whose link is already stored are skipped.
    `now` (default: current UTC time) becomes first_seen for new stories.
    """
    now = now or datetime.now(UTC)
    if not articles:
        return SaveResult(0, 0, 0)

    # Oldest first, so a story's first article sets its title and summary.
    articles = sorted(articles, key=lambda a: a.published)

    # Load stored articles that could match any incoming one (within MAX_GAP).
    candidates = _stored_articles_since(conn, articles[0].published - MAX_GAP)

    new_articles = new_stories = skipped = 0
    for article in articles:
        link_key = normalise_link(article.link)

        # 1. This source already gave us this link (e.g. the fetcher ran again): skip.
        if conn.execute("SELECT 1 FROM story_sources WHERE source = ? AND link_key = ?",
                        (article.source, link_key)).fetchone():
            skipped += 1
            continue

        # 2. Find the story it belongs to, or create a new one.
        story_id = _find_story(article, candidates)
        if story_id is None:
            cursor = conn.execute(
                "INSERT INTO stories (title, summary, category, first_seen) VALUES (?, ?, ?, ?)",
                (article.title, article.summary, article.category, _iso(now)),
            )
            story_id = cursor.lastrowid
            new_stories += 1
        else:
            # Fill in a summary if the story's first source didn't have one.
            conn.execute("UPDATE stories SET summary = ? WHERE id = ? AND summary = ''",
                         (article.summary, story_id))

        # 3. Record this source for the story and refresh the distinct-source count.
        conn.execute(
            "INSERT INTO story_sources (source, link_key, story_id, link, title, category, "
            "published) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (article.source, link_key, story_id, article.link, article.title,
             article.category, _iso(article.published)),
        )
        conn.execute(
            "UPDATE stories SET source_count = (SELECT COUNT(DISTINCT source) FROM story_sources "
            "WHERE story_id = ?) WHERE id = ?",
            (story_id, story_id),
        )

        # 4. Later articles in this same batch can match this one too.
        candidates.append((story_id, article))
        new_articles += 1

    conn.commit()
    return SaveResult(new_articles, new_stories, skipped)


def get_stories(conn: sqlite3.Connection, since: datetime | None = None,
                limit: int = 50) -> list[Story]:
    """Return stories (most sources first, then newest), each with its (source, link) list."""
    rows = conn.execute(
        "SELECT * FROM stories WHERE first_seen >= ? "
        "ORDER BY source_count DESC, first_seen DESC, id DESC LIMIT ?",
        (_iso(since) if since else "", limit),
    ).fetchall()

    stories = []
    for row in rows:
        # Collect every source that reported this story, oldest first.
        sources = conn.execute(
            "SELECT source, link FROM story_sources WHERE story_id = ? ORDER BY published",
            (row["id"],),
        ).fetchall()
        stories.append(Story(
            id=row["id"], title=row["title"], summary=row["summary"],
            category=row["category"], first_seen=datetime.fromisoformat(row["first_seen"]),
            source_count=row["source_count"],
            sources=tuple((s["source"], s["link"]) for s in sources),
        ))
    return stories
