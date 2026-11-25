"""Tests for feeds.py: loading and validating feeds.json."""

import json

import pytest

from feeds import load_feeds


def test_real_feeds_json_is_valid():
    feeds = load_feeds()
    assert len(feeds) >= 1
    assert all(f.url.startswith("https://") for f in feeds)


def test_unknown_category_is_rejected(tmp_path):
    bad = tmp_path / "feeds.json"
    bad.write_text(json.dumps([{"name": "X", "url": "https://x.test", "category": "sports"}]))
    with pytest.raises(ValueError, match="unknown category"):
        load_feeds(bad)
