"""Tests for storage.py. Each test uses a fresh SQLite file in a temp folder."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

import fetcher
import storage
from models import Feed
from tests.pairs import NOT_SAME, SAME, T, art

NOW = T + timedelta(hours=9)


@pytest.fixture
def db(tmp_path):
    """An open connection to an empty database."""
    with storage.connect(tmp_path / "test.db") as conn:
        yield conn


# --- The 5 agreed example pairs, saved through storage --------------------------

@pytest.mark.parametrize("a, b", SAME)
def test_same_pair_becomes_one_story_with_two_sources(db, a, b):
    result = storage.save_articles(db, [a, b], NOW)
    assert (result.new_articles, result.new_stories) == (2, 1)

    [story] = storage.get_stories(db)
    assert story.source_count == 2
    assert set(story.sources) == {(a.source, a.link), (b.source, b.link)}


@pytest.mark.parametrize("a, b", NOT_SAME)
def test_different_pair_becomes_two_stories(db, a, b):
    result = storage.save_articles(db, [a, b], NOW)
    assert (result.new_articles, result.new_stories) == (2, 2)
    assert [s.source_count for s in storage.get_stories(db)] == [1, 1]


def test_all_ten_headlines_together_give_seven_stories(db):
    articles = [x for pair in SAME + NOT_SAME for x in pair.values]
    storage.save_articles(db, articles, NOW)
    counts = sorted(s.source_count for s in storage.get_stories(db))
    assert counts == [1, 1, 1, 1, 2, 2, 2]


# --- Fields and re-runs ---------------------------------------------------------

def test_story_fields_come_from_its_first_article(db):
    first = art("Tidal to ban and remove millions of AI-generated tracks", "TechCrunch AI",
                summary="Tidal will remove AI tracks.")
    later = art("Tidal will start banning AI-generated music next month", "The Verge AI", hours=1)
    storage.save_articles(db, [later, first], NOW)

    [story] = storage.get_stories(db)
    assert story.title == first.title
    assert story.summary == "Tidal will remove AI tracks."
    assert story.category == "ai"
    assert story.first_seen == NOW
    assert story.sources[0] == (first.source, first.link)  # oldest first


def test_saving_again_creates_no_duplicates(db):
    articles = [x for pair in SAME for x in pair.values]
    storage.save_articles(db, articles, NOW)

    again = storage.save_articles(db, articles, NOW + timedelta(hours=1))

    assert (again.new_articles, again.new_stories, again.skipped) == (0, 0, 6)
    assert len(storage.get_stories(db)) == 3
    assert db.execute("SELECT COUNT(*) FROM story_sources").fetchone()[0] == 6


def test_later_run_adds_a_source_to_an_existing_story(db):
    storage.save_articles(db, [art("Anthropic breaks below $83 IPO price", "Hacker News")], NOW)
    later = NOW + timedelta(hours=2)
    result = storage.save_articles(
        db, [art("Anthropic shares fall below IPO price", "TechCrunch AI", hours=5)], later)

    assert (result.new_articles, result.new_stories) == (1, 0)
    [story] = storage.get_stories(db)
    assert story.source_count == 2
    assert story.first_seen == NOW  # unchanged by the later run


def test_same_link_with_trailing_slash_is_a_duplicate(db):
    storage.save_articles(db, [art("X launches Y", "Feed A", "https://x.test/post")], NOW)
    result = storage.save_articles(db, [art("X launches Y", "Feed A", "https://x.test/post/")], NOW)
    assert result.skipped == 1


# --- Re-running the real fetcher (HTTP faked with a saved sample feed) ----------

SAMPLE_RSS = (Path(__file__).parent / "samples" / "sample_rss.xml").read_bytes()


@respx.mock
def test_rerunning_fetcher_creates_no_duplicates(tmp_path):
    feed = Feed("Sample Cloud", "https://cloud.test/feed", "cloud")
    respx.get(feed.url).mock(return_value=httpx.Response(200, content=SAMPLE_RSS))
    db_path = tmp_path / "news.db"
    fetch_time = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)

    first = fetcher.run(db_path, [feed], now=fetch_time)
    second = fetcher.run(db_path, [feed], now=fetch_time + timedelta(minutes=30))

    assert (first.new_articles, first.new_stories) == (2, 2)
    assert (second.new_articles, second.new_stories, second.skipped) == (0, 0, 2)
    with storage.connect(db_path) as conn:
        assert len(storage.get_stories(conn)) == 2
