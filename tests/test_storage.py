"""Tests for storage.py. Every test runs twice: on SQLite and on (moto-faked) DynamoDB."""

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


def all_stories(store: storage.Store) -> list:
    """Every story saved by these tests (fixed window, so results don't depend on today's date)."""
    return store.get_stories(since=T - timedelta(days=3), until=NOW + timedelta(days=1))


# --- The 5 agreed example pairs, saved through storage --------------------------

@pytest.mark.parametrize("a, b", SAME)
def test_same_pair_becomes_one_story_with_two_sources(store, a, b):
    result = store.save_articles([a, b], NOW)
    assert (result.new_articles, result.new_stories) == (2, 1)

    [story] = all_stories(store)
    assert story.source_count == 2
    assert set(story.sources) == {(a.source, a.link), (b.source, b.link)}


@pytest.mark.parametrize("a, b", NOT_SAME)
def test_different_pair_becomes_two_stories(store, a, b):
    result = store.save_articles([a, b], NOW)
    assert (result.new_articles, result.new_stories) == (2, 2)
    assert [s.source_count for s in all_stories(store)] == [1, 1]


def test_all_ten_headlines_together_give_seven_stories(store):
    articles = [x for pair in SAME + NOT_SAME for x in pair.values]
    store.save_articles(articles, NOW)
    counts = [s.source_count for s in all_stories(store)]
    assert counts == [2, 2, 2, 1, 1, 1, 1]  # also checks most-sources-first order


# --- Fields and re-runs ---------------------------------------------------------

def test_story_fields_come_from_its_first_article(store):
    first = art("Tidal to ban and remove millions of AI-generated tracks", "TechCrunch AI",
                summary="Tidal will remove AI tracks.")
    later = art("Tidal will start banning AI-generated music next month", "The Verge AI", hours=1)
    store.save_articles([later, first], NOW)

    [story] = all_stories(store)
    assert story.title == first.title
    assert story.summary == "Tidal will remove AI tracks."
    assert story.category == "ai"
    assert story.first_seen == NOW
    assert story.sources[0] == (first.source, first.link)  # oldest first


def test_missing_summary_is_filled_by_a_later_source(store):
    store.save_articles([art("Anthropic breaks below $83 IPO price", "Hacker News")], NOW)
    store.save_articles([art("Anthropic shares fall below IPO price", "TechCrunch AI",
                             hours=5, summary="Shares dipped below the listing price.")], NOW)
    [story] = all_stories(store)
    assert story.summary == "Shares dipped below the listing price."


def test_saving_again_creates_no_duplicates(store):
    articles = [x for pair in SAME for x in pair.values]
    store.save_articles(articles, NOW)

    again = store.save_articles(articles, NOW + timedelta(hours=1))

    assert (again.new_articles, again.new_stories, again.skipped) == (0, 0, 6)
    stories = all_stories(store)
    assert len(stories) == 3
    assert sum(len(s.sources) for s in stories) == 6


def test_later_run_adds_a_source_to_an_existing_story(store):
    store.save_articles([art("Anthropic breaks below $83 IPO price", "Hacker News")], NOW)
    result = store.save_articles(
        [art("Anthropic shares fall below IPO price", "TechCrunch AI", hours=5)],
        NOW + timedelta(hours=2))

    assert (result.new_articles, result.new_stories) == (1, 0)
    [story] = all_stories(store)
    assert story.source_count == 2
    assert story.first_seen == NOW  # unchanged by the later run


def test_same_link_with_trailing_slash_is_a_duplicate(store):
    store.save_articles([art("X launches Y", "Feed A", "https://x.test/post")], NOW)
    result = store.save_articles([art("X launches Y", "Feed A", "https://x.test/post/")], NOW)
    assert result.skipped == 1


def test_get_stories_respects_since(store):
    store.save_articles([art("Anthropic breaks below $83 IPO price", "Hacker News")], NOW)
    assert store.get_stories(since=NOW + timedelta(minutes=1), until=NOW + timedelta(days=1)) == []


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="Unknown STORAGE_BACKEND"):
        with storage.connect(backend="postgres"):
            pass


def test_old_sqlite_file_gives_clear_error(tmp_path):
    import sqlite3

    old = tmp_path / "old.db"
    with sqlite3.connect(old) as conn:  # a v1 database: integer ids, no user_version
        conn.execute("CREATE TABLE stories (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="Delete it"):
        with storage.connect(old, backend="sqlite"):
            pass


# --- Re-running the real fetcher (HTTP faked with a saved sample feed) ----------

SAMPLE_RSS = (Path(__file__).parent / "samples" / "sample_rss.xml").read_bytes()


@respx.mock
def test_rerunning_fetcher_creates_no_duplicates(backend_env):
    feed = Feed("Sample Cloud", "https://cloud.test/feed", "cloud")
    respx.get(feed.url).mock(return_value=httpx.Response(200, content=SAMPLE_RSS))
    fetch_time = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)

    first = fetcher.run([feed], now=fetch_time)
    second = fetcher.run([feed], now=fetch_time + timedelta(minutes=30))

    assert (first.new_articles, first.new_stories) == (2, 2)
    assert (second.new_articles, second.new_stories, second.skipped) == (0, 0, 2)
    with storage.connect() as store:
        assert len(store.get_stories(since=fetch_time - timedelta(days=1),
                                     until=fetch_time + timedelta(days=1))) == 2
