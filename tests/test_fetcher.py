"""Tests for fetcher.py. All HTTP is faked with respx; no live URLs are called."""

import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

import fetcher
from models import Feed

# Saved sample feeds used instead of real publishers.
SAMPLES = Path(__file__).parent / "samples"
RSS = (SAMPLES / "sample_rss.xml").read_bytes()
ATOM = (SAMPLES / "sample_atom.xml").read_bytes()

# Fixed clock so "last 24 hours" means the same thing on every run.
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
SINCE = NOW - fetcher.MAX_AGE

CLOUD = Feed("Sample Cloud", "https://cloud.test/feed", "cloud")
AI = Feed("Sample AI", "https://ai.test/feed", "ai")
BROKEN = Feed("Broken", "https://broken.test/feed", "tech")
SLOW = Feed("Slow", "https://slow.test/feed", "tech")


def test_parse_rss_keeps_only_recent_dated_linked_items():
    articles = fetcher.parse_feed(CLOUD, RSS, SINCE)

    assert [a.link for a in articles] == [
        "https://example.com/bedrock-agent-tools",
        "https://example.com/s3-pricing",
    ]
    first = articles[0]
    assert first.title == "Amazon Bedrock adds new agent tools"
    assert first.source == "Sample Cloud"
    assert first.category == "cloud"


def test_parse_converts_offsets_to_utc_and_strips_titles():
    s3 = fetcher.parse_feed(CLOUD, RSS, SINCE)[1]
    assert s3.published == datetime(2026, 11, 25, 7, 0, tzinfo=UTC)  # 08:00 +01:00
    assert s3.title == "Amazon S3 lowers request pricing"

    [model] = fetcher.parse_feed(AI, ATOM, SINCE)
    assert model.published == datetime(2026, 11, 25, 10, 30, tzinfo=UTC)  # 05:30 -05:00
    assert model.published.tzinfo is UTC


@respx.mock
def test_fetch_all_skips_failures_and_sorts_newest_first(caplog):
    # Two working feeds, one returning HTTP 429, one timing out.
    respx.get(CLOUD.url).mock(return_value=httpx.Response(200, content=RSS))
    respx.get(AI.url).mock(return_value=httpx.Response(200, content=ATOM))
    respx.get(BROKEN.url).mock(return_value=httpx.Response(429))
    respx.get(SLOW.url).mock(side_effect=httpx.ReadTimeout("timed out"))

    with caplog.at_level(logging.WARNING, logger="fetcher"):
        articles = fetcher.fetch_recent([CLOUD, BROKEN, AI, SLOW], now=NOW)

    # Articles from both working feeds, merged and newest first.
    assert [a.published.hour for a in articles] == [10, 9, 7]
    assert {a.source for a in articles} == {"Sample Cloud", "Sample AI"}

    # Both failures were logged by name.
    assert "Skipping Broken" in caplog.text
    assert "Skipping Slow" in caplog.text


def test_client_uses_ten_second_timeout():
    assert fetcher.TIMEOUT_SECONDS == 10.0


@pytest.mark.parametrize("now, expected", [
    (NOW, 2),                                       # both recent items in window
    (datetime(2026, 11, 26, 8, 30, tzinfo=UTC), 1),  # 07:00 item now >24h old
])
def test_window_is_24_hours(now, expected):
    assert len(fetcher.parse_feed(CLOUD, RSS, now - fetcher.MAX_AGE)) == expected
