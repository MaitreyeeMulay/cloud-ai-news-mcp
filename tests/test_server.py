"""Tests for server.py through the MCP protocol, using the SDK's in-process client.
Each test uses a temporary database seeded with saved headlines; no live calls."""

import json
import re
from datetime import UTC, datetime

import pytest
from mcp import Client

import server
import storage
from models import Story
from tests.pairs import NOT_SAME, SAME, art


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Temp DB with the 5 example pairs (3 two-source stories, 4 one-source stories)."""
    db_path = tmp_path / "news.db"
    articles = [x for pair in SAME + NOT_SAME for x in pair.values]
    # Techmeme copy of the Tidal story, to check "Full story" prefers the original publisher.
    articles.append(art("Tidal to ban AI-generated music, removing millions of tracks (Verge)",
                        "Techmeme", "https://www.techmeme.com/261124/p20#a261124p20", hours=-1))
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("NEWS_DB", str(db_path))
    with storage.connect() as store:
        store.save_articles(articles, now=datetime.now(UTC))
    return db_path


def _tool_result(result) -> list[dict]:
    """Pull the returned list out of a CallToolResult."""
    if result.structured_content is not None:
        return result.structured_content["result"]
    return json.loads(result.content[0].text)


# --- get_top_stories tool --------------------------------------------------------

@pytest.mark.anyio
async def test_get_top_stories_ranks_by_source_count(seeded_db):
    async with Client(server.mcp) as client:
        stories = _tool_result(await client.call_tool("get_top_stories", {"count": 5}))

    assert len(stories) == 5
    counts = [s["source_count"] for s in stories]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 3  # Tidal: The Verge, TechCrunch, Techmeme
    assert {"title", "summary", "source_count", "link", "sources"} <= stories[0].keys()


@pytest.mark.anyio
async def test_full_story_link_skips_aggregators(seeded_db):
    async with Client(server.mcp) as client:
        [tidal] = _tool_result(await client.call_tool("get_top_stories", {"count": 1}))

    # The Techmeme copy is the oldest source, but the link should be the original publisher.
    assert tidal["sources"][0]["source"] == "Techmeme"
    assert "techmeme.com" not in tidal["link"]


def test_primary_link_falls_back_to_aggregator_when_alone():
    story = Story(id="1", title="t", summary="", category="tech",
                          first_seen=datetime.now(UTC), source_count=1,
                          sources=(("Techmeme", "https://www.techmeme.com/x"),))
    assert server.primary_link(story) == "https://www.techmeme.com/x"


# --- reel_scripts prompt ---------------------------------------------------------

async def _prompt_text(arguments: dict[str, str] | None = None) -> str:
    async with Client(server.mcp) as client:
        result = await client.get_prompt("reel_scripts", arguments)
    return result.messages[0].content.text


@pytest.mark.anyio
async def test_reel_scripts_prompt_is_listed():
    async with Client(server.mcp) as client:
        prompts = (await client.list_prompts()).prompts
    [prompt] = [p for p in prompts if p.name == "reel_scripts"]
    assert [a.name for a in prompt.arguments] == ["count"]
    assert not prompt.arguments[0].required  # has a default


@pytest.mark.anyio
async def test_count_defaults_to_5_and_accepts_a_value():
    assert "get_top_stories tool with count=5" in await _prompt_text()
    # MCP sends prompt arguments as strings; "3" must still work.
    assert "get_top_stories tool with count=3" in await _prompt_text({"count": "3"})


@pytest.mark.anyio
async def test_prompt_contains_format_and_rules():
    text = await _prompt_text()
    for line in ("HOOK (0-3s):", "WHAT HAPPENED (3-15s):", "WHY IT MATTERS (15-25s):",
                 "CTA (25-30s):", "On-screen text: <3-5 words>", "Verified by: <source_count>",
                 "Full story: <link>"):
        assert line in text
    assert "At most 75 spoken words" in text
    assert 'start the HOOK with "Unconfirmed:"' in text
    assert "No hype words" in text
    assert server.REEL_EXAMPLE in text


# --- The example script must itself follow the rules ----------------------------

BEATS = ["HOOK (0-3s):", "WHAT HAPPENED (3-15s):", "WHY IT MATTERS (15-25s):", "CTA (25-30s):"]


def test_example_follows_the_format():
    lines = server.REEL_EXAMPLE.splitlines()
    beat_lines = [ln for ln in lines if any(ln.startswith(b) for b in BEATS)]
    assert [ln.split(":")[0] + ":" for ln in beat_lines] == BEATS  # all 4 beats, in order

    on_screen = [ln.removeprefix("On-screen text: ") for ln in lines
                 if ln.startswith("On-screen text:")]
    assert len(on_screen) == 4
    assert all(3 <= len(t.split()) <= 5 for t in on_screen)

    assert re.search(r"^Verified by: \d+ sources?$", server.REEL_EXAMPLE, re.M)
    assert re.search(r"^Full story: https://\S+$", server.REEL_EXAMPLE, re.M)


def test_example_has_at_most_75_spoken_words():
    spoken = " ".join(ln.split(":", 1)[1] for ln in server.REEL_EXAMPLE.splitlines()
                      if any(ln.startswith(b) for b in BEATS))
    assert len(spoken.split()) <= 75
