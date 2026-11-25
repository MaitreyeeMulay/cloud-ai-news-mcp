"""SQLite storage backend, for local use. See storage.py for the interface.

Two tables:
  stories        one row per story (title, summary, category, first_seen, source_count)
  story_sources  one row per article that reported it; primary key (source, link_key),
                 so the same source can't store the same article twice.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from models import Article, Story

# Bump when the tables change, so an old news.db gives a clear error.
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id           TEXT    PRIMARY KEY,        -- storage.new_story_id()
    title        TEXT    NOT NULL,
    summary      TEXT    NOT NULL DEFAULT '',
    category     TEXT    NOT NULL,
    first_seen   TEXT    NOT NULL,           -- ISO 8601 UTC: when we first saved it
    source_count INTEGER NOT NULL            -- distinct sources in story_sources
);
CREATE TABLE IF NOT EXISTS story_sources (
    source    TEXT NOT NULL,
    link_key  TEXT NOT NULL,                 -- dedup.normalise_link(link)
    story_id  TEXT NOT NULL REFERENCES stories(id),
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
CREATE INDEX IF NOT EXISTS idx_stories_seen      ON stories(first_seen);
"""


def _iso(dt: datetime) -> str:
    """Store datetimes as UTC ISO strings, which also sort correctly as text."""
    return dt.astimezone(UTC).isoformat()


class SqliteBackend:
    """storage.Backend on a local SQLite file."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    @contextmanager
    def open(cls, path: Path | str) -> Iterator["SqliteBackend"]:
        """Open (and if needed create) the database; commit and close on exit."""
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            cls._ensure_schema(conn, path)
            yield cls(conn)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection, path: Path | str) -> None:
        """Create tables in a new file; refuse a file made by an older version."""
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has_tables = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stories'").fetchone()
        if has_tables and version != SCHEMA_VERSION:
            raise RuntimeError(
                f"{path} was created by an older version of this project. "
                "Delete it and run `python app/fetcher.py` to rebuild it."
            )
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def load_articles_since(self, since: datetime, until: datetime) -> list[tuple[str, Article]]:
        """Stored articles published in [since, until], with their story ids."""
        rows = self.conn.execute(
            "SELECT story_id, source, link, title, category, published FROM story_sources "
            "WHERE published >= ? AND published <= ?",
            (_iso(since), _iso(until)),
        ).fetchall()
        return [
            (row["story_id"], Article(title=row["title"], link=row["link"], source=row["source"],
                                      category=row["category"],
                                      published=datetime.fromisoformat(row["published"])))
            for row in rows
        ]

    def add_article(self, story_id: str, article: Article, link_key: str) -> bool:
        """Insert one article row; INSERT OR IGNORE returns 0 rows changed for a duplicate."""
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO story_sources (source, link_key, story_id, link, title, "
            "category, published) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (article.source, link_key, story_id, article.link, article.title,
             article.category, _iso(article.published)),
        )
        return cursor.rowcount == 1

    def create_story(self, story_id: str, article: Article, now: datetime) -> None:
        """Insert a story row with this article as its only source."""
        self.conn.execute(
            "INSERT INTO stories (id, title, summary, category, first_seen, source_count) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (story_id, article.title, article.summary, article.category, _iso(now)),
        )

    def update_story(self, story_id: str, article: Article, source_count: int) -> None:
        """Set the new source count; take this article's summary if the story has none."""
        self.conn.execute(
            "UPDATE stories SET source_count = ?, "
            "summary = CASE WHEN summary = '' THEN ? ELSE summary END WHERE id = ?",
            (source_count, article.summary, story_id),
        )

    def get_stories(self, since: datetime, until: datetime, limit: int) -> list[Story]:
        """Stories first seen in [since, until], most sources first, with their sources."""
        rows = self.conn.execute(
            "SELECT * FROM stories WHERE first_seen >= ? AND first_seen <= ? "
            "ORDER BY source_count DESC, first_seen DESC, id DESC LIMIT ?",
            (_iso(since), _iso(until), limit),
        ).fetchall()

        stories = []
        for row in rows:
            # Every source that reported this story, oldest first.
            sources = self.conn.execute(
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
