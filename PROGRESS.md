# Progress

## Session 1: 2026-11-24/25

### Done
- **feeds.json**: 13 sources, each tested live (4 cloud, 7 ai, 2 tech).
  Categories are `cloud`, `ai`, `tech` (tech = Techmeme, Hacker News: general news).
- **models.py**: `Feed`, `Article` (title, link, source, category, published UTC, summary), `Story`.
- **feeds.py**: loads feeds.json and rejects unknown categories.
- **fetcher.py**: fetches all feeds at once (10 s timeout each), logs and skips failed
  feeds, keeps the last 24 h, cleans summaries to 300 chars. `python fetcher.py` fetches and
  saves to SQLite.
- **dedup.py**: same-story rules: same source never matches; more than 48 h apart never
  matches; same normalised link matches; or ≥ 3 shared title keywords that make up ≥ 50% of
  the shorter title's keywords. Uses stopwords, date removal and light stemming. No ML.
- **storage.py**: SQLite `news.db` (git-ignored; set `NEWS_DB` to move it). Tables `stories`
  and `story_sources`, primary key `(source, link_key)`, so re-running the fetcher adds no
  duplicates.
- **Tests**: 31 passing (`pytest`), no live calls. The 5 agreed headline pairs are in
  `tests/pairs.py`.
- Live check: 77 articles became 71 stories, 5 of them with 2+ sources; a second run added 0.

### Decisions
- **Stored, not live search**: a scheduled fetch writes to the DB and MCP tools only read.
  Reasons: story counting needs history, cost is €0 whatever the traffic, fast replies,
  less load on publishers. See the comparison at https://claude.ai/artifact/2cvKQniyppBmGSaHNfsuCn
- **MCP SDK v2** (`from mcp.server.mcpserver import MCPServer`), because v2 renamed FastMCP.
- **"Same event, different angle" stays separate** (e.g. different Jensen Huang stories):
  avoiding wrong merges matters more than catching every match.
- **NewsAPI left out**: its free plan is for development only.

### Source workarounds (check these still work)
- **Anthropic**: no official RSS; uses the third-party `Olshansk/rss-feeds` GitHub feed.
  Fallback: the official `anthropic.com/sitemap.xml`.
- **Meta AI**: `ai.meta.com` has no feed; uses Engineering at Meta, AI Research category.
- **VentureBeat**: blocks automated readers (429); uses a Google News search feed. Links are
  `news.google.com` redirects.

### Next
1. **server.py**: MCP tools that read from storage (e.g. top stories, search, list sources).
   Add `mcp` to requirements.txt. Test with `npx @modelcontextprotocol/inspector python server.py`.
2. Reel scripts: turn the top stories into 15–30 s scripts with a link to the full story.
3. AWS: SAM template, Lambda + EventBridge Scheduler (fetch every 2 h), DynamoDB
   (PROVISIONED 1 RCU/1 WCU, date-based key so searches use Query, not Scan), Streamable HTTP.

### Open problems
- **Reddit (r/aws, r/LocalLLaMA) on hold**: needs a Reddit API app (OAuth). On AWS, store
  the keys in SSM Parameter Store (free), not Secrets Manager (about $0.40 per secret per month).
- **Are aggregators independent?** Hacker News and Techmeme often just link to the original,
  which inflates source counts (e.g. GPT-6: OpenAI plus 2 aggregators).
- **Google News redirect links** (VentureBeat): resolve them to the real URL for reel links
  and better link matching.
- **Story titles** come from the first article and can keep prefixes such as "Sources: …".
  Clean them before writing reel scripts.
- If a new article matches two stored stories, it joins the better match; the two stories
  are not merged.
- The VS Code warning about `feedparser` goes away once the interpreter is set to `.venv`.
