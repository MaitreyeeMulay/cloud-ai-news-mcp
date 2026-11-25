"""Download all feeds at once and return the articles from the last 24 hours.

Run directly to fetch and save into local storage:  python app/fetcher.py
"""

import asyncio
import html
import logging
import re
from calendar import timegm
from datetime import UTC, datetime, timedelta

import feedparser
import httpx

import storage
from feeds import load_feeds
from models import Article, Feed

log = logging.getLogger(__name__)

# Identify ourselves honestly to publishers; some block requests without a User-Agent.
USER_AGENT = "cloud-ai-news-mcp/0.1 (RSS reader)"
# Give up on any single feed after this many seconds.
TIMEOUT_SECONDS = 10.0
# Only keep articles published within this window.
MAX_AGE = timedelta(hours=24)
# Longest summary we keep; feeds sometimes put the whole article in it.
SUMMARY_CHARS = 300

_TAG_RE = re.compile(r"<[^>]+>")


def clean_summary(raw: str) -> str:
    """Turn a feed's HTML summary into short plain text."""
    # Remove HTML tags, decode entities like &amp;, and collapse whitespace.
    text = html.unescape(_TAG_RE.sub(" ", raw or ""))
    text = " ".join(text.split())
    # Cut at a word boundary and mark the cut with an ellipsis.
    if len(text) > SUMMARY_CHARS:
        text = text[: SUMMARY_CHARS - 1].rsplit(" ", 1)[0] + "…"
    return text


def _published_utc(entry: feedparser.FeedParserDict) -> datetime | None:
    """Return the entry's publish time as UTC, or None if the feed gives no date.

    feedparser already converts every date format to a UTC time tuple;
    timegm turns that tuple into a timestamp without applying the local timezone.
    """
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return None
    return datetime.fromtimestamp(timegm(parsed), tz=UTC)


def parse_feed(feed: Feed, content: bytes, since: datetime) -> list[Article]:
    """Turn raw RSS/Atom bytes into Articles published at or after `since`.

    Entries without a link or a date are skipped: without a date we can't
    tell whether they are recent.
    """
    parsed = feedparser.parse(content)
    articles = []
    for entry in parsed.entries:
        # Skip entries we can't link to or can't date.
        link = entry.get("link")
        published = _published_utc(entry)
        if not link or published is None:
            continue
        # Skip entries older than the window.
        if published < since:
            continue
        articles.append(
            Article(
                title=entry.get("title", "").strip(),
                link=link,
                source=feed.name,
                category=feed.category,
                published=published,
                summary=clean_summary(entry.get("summary", "")),
            )
        )
    return articles


async def fetch_feed(client: httpx.AsyncClient, feed: Feed, since: datetime) -> list[Article]:
    """Download and parse one feed. On any network or HTTP error, log it and return []."""
    try:
        # Download the feed; raise_for_status turns 4xx/5xx (e.g. 429) into an exception.
        response = await client.get(feed.url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # One broken feed must not stop the others.
        log.warning("Skipping %s: %s", feed.name, exc)
        return []

    articles = parse_feed(feed, response.content, since)
    # Zero recent items is normal for slow feeds, but worth seeing in the logs:
    # it is also how a stale or broken third-party feed shows up.
    if not articles:
        log.info("%s: no articles in the last %d hours", feed.name, MAX_AGE // timedelta(hours=1))
    return articles


async def fetch_all(feeds: list[Feed], now: datetime | None = None) -> list[Article]:
    """Fetch every feed concurrently and return recent articles, newest first.

    `now` exists so tests can pin the clock; normally it is the current UTC time.
    """
    since = (now or datetime.now(UTC)) - MAX_AGE

    # One shared client reuses connections; timeout applies to each request.
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        # Start all downloads at once and wait for all of them to finish.
        results = await asyncio.gather(*(fetch_feed(client, f, since) for f in feeds))

    # Flatten the per-feed lists and sort newest first.
    articles = [article for feed_articles in results for article in feed_articles]
    articles.sort(key=lambda a: a.published, reverse=True)
    return articles


def fetch_recent(feeds: list[Feed] | None = None, now: datetime | None = None) -> list[Article]:
    """Synchronous wrapper around fetch_all; uses feeds.json when no feeds are given."""
    return asyncio.run(fetch_all(feeds if feeds is not None else load_feeds(), now))


def run(feeds: list[Feed] | None = None, now: datetime | None = None) -> storage.SaveResult:
    """Fetch recent articles and save them as stories. Safe to run repeatedly.

    Storage comes from the environment (STORAGE_BACKEND, NEWS_DB / TABLE_NAME).
    """
    articles = fetch_recent(feeds, now)
    with storage.connect() as store:
        return store.save_articles(articles, now)


if __name__ == "__main__":
    # Show warnings (skipped feeds) and info (empty feeds) on the console.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Keep httpx from logging every single request.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    result = run()
    print(f"Saved {result.new_articles} new articles "
          f"({result.new_stories} new stories, {result.skipped} already stored)")
