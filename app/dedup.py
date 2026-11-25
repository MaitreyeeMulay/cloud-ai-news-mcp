"""Group articles from different sources that report the same story.

No ML: two articles are the same story if they share a link, or if their
titles share enough keywords. See same_story() for the exact rules.
"""

import html
import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit

from models import Article

# --- Tuning knobs -----------------------------------------------------------
# Keyword rule: at least this many shared keywords...
MIN_SHARED = 3
# ...and they must make up at least this share of the shorter title's keywords.
MIN_RATIO = 0.5
# Articles published further apart than this are never the same story.
MAX_GAP = timedelta(hours=48)

# Words that carry no meaning about *which* story it is. Includes generic news
# verbs and "ai", which appears in almost every title from the AI feeds.
STOPWORDS = frozenset("""
a an the and or but if than then so not no of to in on for with by at from as into over
about after before up out is are was were be been being it its this that these those
i you he she we they him her his their our your them who what when where which why how
here there has have had do does did can could will would may might should just still also
new now more most very only even via vs
ai says say said reports report reportedly sources source
brings bring adds add launches launch announces announce gets get makes make lets let
""".split())

# Month names and years: two unrelated "November 24, 2026" posts must not match on the date.
MONTHS = frozenset("""
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
""".split())
YEAR_RE = re.compile(r"^(19|20)\d\d$")

# Query parameters that only track clicks; they never identify the article.
TRACKING_PREFIXES = ("utm_", "mc_")
TRACKING_KEYS = frozenset({"fbclid", "gclid", "ref", "oc"})

# Leading tags like "[Launched]", "[In preview]", "Sources:", "Show HN:".
PREFIX_RE = re.compile(r"^(\s*\[[^\]]*\]\s*|\s*(sources|show hn|launch hn|ask hn)\s*:\s*)+")
# Trailing publisher in brackets, e.g. Techmeme's "... (New York Times)".
TRAILING_PARENS_RE = re.compile(r"\s*\([^()]*\)\s*$")
# Trailing " - Publisher", which Google News adds to every title.
GOOGLE_NEWS_SUFFIX_RE = re.compile(r"\s+-\s+[^-]+$")


def normalise_link(url: str) -> str:
    """Reduce a URL so trivially different links to the same page compare equal.

    Drops the scheme, "www.", #fragment, trailing slash and tracking parameters,
    but keeps real query parameters, because some feeds identify the article
    there (Azure: ".../updates?id=582171").
    "https://www.x.com/a/?utm_source=t&id=7#top" -> "x.com/a?id=7".
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    # Keep non-tracking query parameters, sorted so their order doesn't matter.
    params = sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_PREFIXES) and key.lower() not in TRACKING_KEYS
    )
    query = f"?{urlencode(params)}" if params else ""
    return f"{host}{parts.path.rstrip('/')}{query}"


def _stem(word: str) -> str:
    """Very light stemming so word forms match: banning/ban, shares/shared/share."""
    # Plurals: "companies" -> "company", "boxes" -> "box", "tracks" -> "track".
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("sses", "xes", "ches", "shes", "zes")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    # Verb endings: "banning" -> "bann", "breached" -> "breach".
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            # Undouble a final consonant left behind: "bann" -> "ban" (but keep "pass", "call").
            if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "lsz":
                word = word[:-1]
            break
    # Drop a final "e" so "share" and "shar(ed)" end up identical.
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    return word


def title_keywords(article: Article) -> set[str]:
    """Turn an article's title into a set of meaningful, stemmed keywords."""
    # Decode HTML entities (&#8217;) and unify curly quotes, then lowercase.
    title = html.unescape(article.title).replace("’", "'").replace("‘", "'").lower()

    # Remove decorations that aren't part of the headline itself.
    title = PREFIX_RE.sub("", title)
    title = TRAILING_PARENS_RE.sub("", title)
    if "news.google.com" in article.link:
        title = GOOGLE_NEWS_SUFFIX_RE.sub("", title)

    # Split on anything that isn't a letter or digit: "protein-design" -> protein, design.
    words = re.findall(r"[a-z0-9]+", title)

    keywords = set()
    for word in words:
        # Skip filler words, dates and single letters (keep single digits like the 6 in GPT-6).
        if word in STOPWORDS or word in MONTHS or YEAR_RE.match(word):
            continue
        if len(word) == 1 and not word.isdigit():
            continue
        keywords.add(_stem(word))
    return keywords


def same_story(a: Article, b: Article) -> bool:
    """Decide whether two articles report the same story. Rules, in order:

    1. Same source -> never (we count *independent* sources).
    2. Published more than MAX_GAP apart -> never.
    3. Same normalised link -> yes.
    4. Titles share >= MIN_SHARED keywords that make up >= MIN_RATIO of the
       shorter title's keywords -> yes.
    """
    # Rule 1: one feed can't confirm its own story.
    if a.source == b.source:
        return False

    # Rule 2: stories far apart in time are different stories.
    if abs(a.published - b.published) > MAX_GAP:
        return False

    # Rule 3: both point at the same page.
    if normalise_link(a.link) == normalise_link(b.link):
        return True

    # Rule 4: enough keyword overlap, measured against the shorter title
    # so a short headline can match a long, detailed one.
    keywords_a, keywords_b = title_keywords(a), title_keywords(b)
    shared = keywords_a & keywords_b
    smaller = min(len(keywords_a), len(keywords_b))
    if smaller == 0:
        return False
    return len(shared) >= MIN_SHARED and len(shared) / smaller >= MIN_RATIO


def group_stories(articles: list[Article]) -> list[list[Article]]:
    """Group articles into stories.

    Compares every pair, and joins matches with union-find so that if A matches B
    and B matches C, all three land in one group. Returns groups (each sorted
    newest first), ordered by number of distinct sources, then by newest article.
    """
    # Union-find: parent[i] points towards the representative item of i's group.
    parent = list(range(len(articles)))

    def find(i: int) -> int:
        # Follow parent links to the group's representative, shortening the path as we go.
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Compare each pair once; merge the two groups when they are the same story.
    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            if same_story(articles[i], articles[j]):
                parent[find(i)] = find(j)

    # Collect articles by their group representative.
    groups: dict[int, list[Article]] = {}
    for i, article in enumerate(articles):
        groups.setdefault(find(i), []).append(article)

    # Sort inside each group (newest first), then sort the groups themselves.
    result = [sorted(g, key=lambda a: a.published, reverse=True) for g in groups.values()]
    result.sort(key=lambda g: (len({a.source for a in g}), g[0].published), reverse=True)
    return result
