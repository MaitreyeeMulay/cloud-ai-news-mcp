"""Load the list of news sources from feeds.json."""

import json
from pathlib import Path
from typing import get_args

from models import Category, Feed

# feeds.json sits next to this file, wherever the project is run from.
FEEDS_FILE = Path(__file__).with_name("feeds.json")


def load_feeds(path: Path = FEEDS_FILE) -> list[Feed]:
    """Read feeds.json and return one Feed per entry.

    Raises ValueError if an entry has an unknown category, so a typo in
    feeds.json fails loudly instead of silently dropping a source.
    """
    # Read the JSON list of {"name", "url", "category"} objects.
    entries = json.loads(path.read_text(encoding="utf-8"))

    feeds = []
    for entry in entries:
        # Reject categories other than the ones defined in models.Category.
        if entry["category"] not in get_args(Category):
            raise ValueError(f"{entry['name']}: unknown category {entry['category']!r}")
        feeds.append(Feed(name=entry["name"], url=entry["url"], category=entry["category"]))
    return feeds
