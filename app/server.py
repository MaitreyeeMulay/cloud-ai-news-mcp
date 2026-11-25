"""MCP server: read-only tools over the stored stories, plus the reel_scripts prompt.

Run locally on stdio:  python app/server.py
It never fetches feeds itself; run `python app/fetcher.py` (or the scheduler) to fill storage.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer

import storage
from models import Story

# Sites that mostly link to other publishers' stories. For "Full story" we prefer the
# original publisher's link and use these only when nothing else reported the story.
AGGREGATOR_HOSTS = ("techmeme.com", "news.ycombinator.com", "news.google.com")

mcp = MCPServer(
    "cloud-ai-news",
    instructions=(
        "Cloud and AI news stories, de-duplicated across sources and ranked by how many "
        "independent sources reported them. Data is refreshed on a schedule, not live."
    ),
)


def primary_link(story: Story) -> str:
    """Pick the link for "Full story": the oldest non-aggregator source, else the oldest one."""
    for _source, link in story.sources:
        host = urlsplit(link).netloc.lower().removeprefix("www.")
        if not host.endswith(AGGREGATOR_HOSTS):
            return link
    return story.sources[0][1]


def story_to_dict(story: Story) -> dict:
    """Shape a Story for tool output: plain JSON types only."""
    return {
        "id": story.id,
        "title": story.title,
        "summary": story.summary,
        "category": story.category,
        "first_seen": story.first_seen.isoformat(),
        "source_count": story.source_count,
        "link": primary_link(story),
        "sources": [{"source": s, "link": link} for s, link in story.sources],
    }


@mcp.tool()
def get_top_stories(
    count: int = 5,
    hours: int = 24,
    category: Literal["cloud", "ai", "tech"] | None = None,
) -> list[dict]:
    """Get the most widely reported cloud/AI/tech news stories from the last `hours` hours.

    Use this to find out what the biggest recent stories are, e.g. before writing
    summaries or reel scripts. Stories are ranked by source_count (how many
    independent outlets reported the same story), then by newest. Each story has
    a title, a short summary, its source_count, `link` (the best link to the full
    story) and every (source, link) that reported it.

    Args:
        count: How many stories to return (1-20).
        hours: Only stories first seen within this many hours (1-168).
        category: "cloud", "ai" or "tech". Omit for all categories.
    """
    # Keep inputs in a sensible range so one call can't request the whole database.
    count = max(1, min(count, 20))
    since = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 168)))

    # Over-fetch when filtering by category, then trim to `count`.
    with storage.connect() as store:
        stories = store.get_stories(since=since, limit=count if category is None else 500)
    if category is not None:
        stories = [s for s in stories if s.category == category]
    return [story_to_dict(s) for s in stories[:count]]


REEL_EXAMPLE = """\
HOOK (0-3s): Tidal is kicking AI-generated music off its platform.
On-screen text: Tidal bans AI music

WHAT HAPPENED (3-15s): Starting next month, the streaming service will ban AI-generated tracks and remove millions that are already uploaded.
On-screen text: Millions of tracks removed

WHY IT MATTERS (15-25s): AI songs are flooding streaming apps and competing with human artists for listeners and royalties. Other services may face pressure to follow.
On-screen text: Artists versus the AI flood

CTA (25-30s): Would you want this on your app? Full story linked below.
On-screen text: Link in description

Verified by: 2 sources
Full story: https://www.theverge.com/entertainment/1041362/tidal-ai-music-ban"""


@mcp.prompt(title="Reel scripts for top stories")
def reel_scripts(count: int = 5) -> str:
    """Write 15-30 second reel scripts for the top cloud and AI news stories."""
    # Keep the request within what get_top_stories allows.
    count = max(1, min(count, 20))
    return f"""\
Call the get_top_stories tool with count={count}. Then write one reel script for each \
story it returns, in the same order.

Use exactly this format for every script:

HOOK (0-3s): <spoken line>
On-screen text: <3-5 words>

WHAT HAPPENED (3-15s): <spoken lines>
On-screen text: <3-5 words>

WHY IT MATTERS (15-25s): <spoken lines>
On-screen text: <3-5 words>

CTA (25-30s): <spoken line>
On-screen text: <3-5 words>

Verified by: <source_count> sources
Full story: <link>

Rules:
- At most 75 spoken words per script (count only the four spoken beats, not the \
on-screen text or the last two lines).
- Plain English a non-technical viewer understands. Short sentences.
- No hype words, such as: game-changer, revolutionary, groundbreaking, insane, \
mind-blowing, shocking, huge, unprecedented.
- Use only facts from the story's title and summary. Do not add numbers, dates, names \
or claims that are not there. If something is unclear, leave it out.
- If source_count is 1, start the HOOK with "Unconfirmed:" and write \
"Verified by: 1 source".
- "Verified by" uses the story's source_count. "Full story" uses the story's `link` field.
- Separate scripts with a line containing only "---".

Example of one finished script:

{REEL_EXAMPLE}"""


def main() -> None:
    """Start the server on stdio (how Claude Desktop / Claude Code launch it locally)."""
    # Logs go to stderr; stdout carries the MCP protocol and must stay clean.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
