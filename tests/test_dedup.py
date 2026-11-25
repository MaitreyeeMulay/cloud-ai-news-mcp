"""Tests for dedup.py matching rules, using real headlines saved as strings."""

import pytest

from dedup import group_stories, normalise_link, same_story, title_keywords
from tests.pairs import NOT_SAME, SAME, art

# --- The 5 agreed example pairs --------------------------------------------------


@pytest.mark.parametrize("a, b", SAME)
def test_same_story(a, b):
    assert same_story(a, b)
    assert same_story(b, a)  # order must not matter


@pytest.mark.parametrize("a, b", NOT_SAME)
def test_not_same_story(a, b):
    assert not same_story(a, b)
    assert not same_story(b, a)


# --- Extra regressions -----------------------------------------------------------

def test_dates_alone_do_not_match():
    # Found in live data: matched only on "November 24 2026" before the date rule.
    roundup = art("AWS Weekly Roundup: Local Zone in Bogotá, 30-minute Lambda timeout, and more "
                  "(November 24, 2026)", "AWS News Blog")
    release_notes = art("November 24, 2026", "Google Cloud Release Notes")
    assert not same_story(roundup, release_notes)


def test_same_source_never_matches():
    a = art("Public Preview: Optimize agents from insights in Foundry Agent Service", "Azure")
    b = art("Public Preview: Native A2A passthrough for hosted agents in Foundry Agent Service",
            "Azure")
    assert not same_story(a, b)


def test_more_than_48_hours_apart_never_matches():
    a = art("Tidal will start banning AI-generated music next month", "The Verge AI")
    b = art("Tidal will start banning AI-generated music next month", "TechCrunch AI", hours=49)
    assert not same_story(a, b)


def test_publisher_suffixes_are_ignored():
    techmeme = art("OpenAI says GPT-6 models design proteins (New York Times)", "Techmeme")
    google = art("Zipher Raises $53M - VentureBeat", "VentureBeat",
                 "https://news.google.com/rss/articles/abc")
    assert {"york", "tim"}.isdisjoint(title_keywords(techmeme))
    assert "venturebeat" not in title_keywords(google)


def test_normalise_link_drops_tracking_but_keeps_real_params():
    assert normalise_link("https://www.Example.com/a/b/?utm_source=x#top") == "example.com/a/b"
    assert normalise_link("https://x.com/a?b=2&a=1&utm_medium=rss") == "x.com/a?a=1&b=2"
    assert normalise_link("https://x.com/a?ref=feed&reference=9") == "x.com/a?reference=9"


def test_azure_update_ids_stay_distinct():
    # Found in live data: dropping the whole query merged all Azure updates into one link.
    assert (normalise_link("https://azure.microsoft.com/updates?id=582171")
            != normalise_link("https://azure.microsoft.com/updates?id=581483"))


def test_group_stories_joins_three_sources_and_ranks_first():
    articles = [
        art("Tidal will start banning AI-generated music next month", "The Verge AI"),
        art("GPT-6 models demonstrate intrinsic protein-design capabilities", "OpenAI News",
            "https://openai.com/index/gpt-6-protein"),
        art("GPT-6 models demonstrate intrinsic protein-design capabilities", "Hacker News",
            "https://openai.com/index/gpt-6-protein/"),
        art("OpenAI says GPT-6 models can design new protein shapes and sequences using only "
            "reasoning and code (OpenAI)", "Techmeme", hours=1),
        art("Jensen Huang deepfakes are the hot new AI slop", "The Verge AI", hours=-1),
    ]
    groups = group_stories(articles)

    # GPT-6 (3 sources) first; Tidal and Jensen stay alone.
    assert [len(g) for g in groups] == [3, 1, 1]
    assert {a.source for a in groups[0]} == {"OpenAI News", "Hacker News", "Techmeme"}
    assert groups[0][0].source == "Techmeme"  # newest first inside the group
