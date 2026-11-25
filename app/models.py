"""Data shapes shared by every module (fetcher, storage, server)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# The only categories a feed or article may have.
Category = Literal["cloud", "ai", "tech"]


@dataclass(frozen=True)
class Feed:
    """One news source from feeds.json."""

    name: str
    url: str
    category: Category


@dataclass(frozen=True)
class Article:
    """One news item from one feed. `published` is always timezone-aware UTC."""

    title: str
    link: str
    source: str
    category: Category
    published: datetime
    summary: str = ""


@dataclass(frozen=True)
class Story:
    """One story, reported by one or more sources (see dedup.py for the matching rules)."""

    id: str
    title: str
    summary: str
    category: Category
    first_seen: datetime
    source_count: int
    # Every (source name, link) that reported this story, oldest first.
    sources: tuple[tuple[str, str], ...]
