"""The 5 agreed example pairs (real headlines from 24 Nov 2026), shared by
test_dedup.py and test_storage.py. Saved as strings: no live calls."""

from datetime import UTC, datetime, timedelta
from zlib import crc32

import pytest

from models import Article

T = datetime(2026, 11, 24, 18, 0, tzinfo=UTC)


def art(title: str, source: str, link: str = "", hours: float = 0, summary: str = "") -> Article:
    """Build a test Article. Without a link it gets a stable dummy one, unique per title+source."""
    slug = source.replace(" ", "").lower()
    return Article(
        title=title,
        link=link or f"https://{slug}.test/{crc32(title.encode())}",
        source=source,
        category="ai",
        published=T + timedelta(hours=hours),
        summary=summary,
    )


SAME = [
    pytest.param(  # 1. Same link, one with a trailing slash.
        art("GPT-6 models demonstrate intrinsic protein-design capabilities", "OpenAI News",
            "https://openai.com/index/gpt-6-models-demonstrate-intrinsic-protein-design-capabilities"),
        art("GPT-6 models demonstrate intrinsic protein-design capabilities", "Hacker News",
            "https://openai.com/index/gpt-6-models-demonstrate-intrinsic-protein-design-capabilities/",
            hours=8),
        id="1-gpt6-shared-link"),
    pytest.param(  # 2. Different wording; needs stemming (banning / ban).
        art("Tidal will start banning AI-generated music next month", "The Verge AI", hours=0.5),
        art("Tidal to ban and remove millions of AI-generated tracks as AI slop inundates "
            "music streaming", "TechCrunch AI"),
        id="2-tidal-stemming"),
    pytest.param(  # 3. Different verbs, a price in one title.
        art("Anthropic shares fall below IPO price, a worrying sign for AI startups",
            "TechCrunch AI", hours=5),
        art("Anthropic breaks below $83 IPO price", "Hacker News"),
        id="3-anthropic-ipo"),
]

NOT_SAME = [
    pytest.param(  # 4. Only the person's name in common.
        art("Jensen Huang still thinks a succession plan creates unnecessary hierarchy",
            "TechCrunch AI", hours=6),
        art("Jensen Huang deepfakes are the hot new AI slop", "The Verge AI"),
        id="4-jensen-name-only"),
    pytest.param(  # 5. Same product, different news. Both were TechCrunch in reality;
        #             different sources here so the keyword rule (not the same-source rule) is tested.
        art("Walmart teams up with Meta to bring Black Friday deals to AI agent Muse",
            "TechCrunch AI", hours=4.5),
        art("Meta brings its Muse AI assistant directly into Instagram DMs", "Other Outlet"),
        id="5-meta-muse-different-news"),
]
