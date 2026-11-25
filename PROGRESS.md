# Progress

## Status (end of session 1, 2026-11-25)
**Built and tested locally, not deployed.** Nothing exists in AWS yet. `sam build` succeeds;
the next step is `sam deploy --guided` (answers below), which needs the user's go-ahead.

## Done
- **Layout**: code shipped to Lambda lives in `app/`; `app/requirements.txt` holds runtime deps
  only (httpx, feedparser). Root `requirements.txt` has everything for local dev (incl. mcp,
  boto3, moto, cfn-lint). `news.db` stays at the root so it is never packaged.
- **app/feeds.json**: 13 sources, each tested live (4 cloud, 7 ai, 2 tech).
- **app/fetcher.py**: fetches all feeds at once (10 s timeout each), logs and skips failed
  feeds, keeps the last 24 h, cleans summaries. `python app/fetcher.py` fetches and saves.
- **app/dedup.py**: same-story rules: same source never matches; > 48 h apart never matches;
  same normalised link matches (tracking params dropped, real params like Azure `?id=` kept);
  or ≥ 3 shared title keywords making up ≥ 50% of the shorter title's. No ML.
- **Storage, two backends behind one interface** (`storage.connect()` → `Store` with
  `save_articles` / `get_stories`), chosen by `STORAGE_BACKEND`:
  - `sqlite` (default, local): `app/storage_sqlite.py`, file from `NEWS_DB` or `news.db`.
    Schema v2 (text story ids); an older `news.db` gives a "delete and rebuild" error.
  - `dynamodb` (Lambda): `app/storage_dynamodb.py`, table from `TABLE_NAME`. One table, no
    indexes; articles under `ADAY#<date>`, stories under `SDAY#<date>`; reads are Query only;
    TTL `expires_at` = 30 days.
  - Matching logic lives once in `app/storage.py`. Re-runs add no duplicates on either backend.
- **app/server.py** (MCP, stdio): tool `get_top_stories(count, hours, category)` (read-only;
  "Full story" link prefers the original publisher over Techmeme/HN/Google News) and prompt
  `reel_scripts(count=5)` (HOOK / WHAT HAPPENED / WHY IT MATTERS / CTA, max 75 spoken words,
  "Unconfirmed:" for single-source stories, Tidal example script).
- **app/lambda_handler.py**: runs fetch + save; boto3 retries throttling (1 WCU).
- **template.yaml** (eu-west-1): DynamoDB PROVISIONED 1/1 RCU/WCU (Retain, TTL, no autoscaling/
  GSI/streams/PITR); Lambda Python 3.12, 256 MB, 60 s, x86_64, not in a VPC; EventBridge
  Scheduler `rate(15 minutes)`; log group with 7-day retention; explicit least-privilege role
  (Query/PutItem/UpdateItem on the table, CreateLogStream/PutLogEvents on its log group);
  optional $1 budget alert if `AlertEmail` is given. `samconfig.toml`: region, stack name,
  changeset confirmation on.
- **Checks**: 57 tests pass (storage tests on SQLite and moto-faked DynamoDB; Lambda handler
  test). `cfn-lint` and `sam validate --lint` pass. `sam build` succeeds: 2.7 MB, pure Python,
  imports with no other packages installed.
- **Tooling**: SAM CLI 1.174.0 installed in `.sam-venv/` (git-ignored). Run with
  `$env:SAM_CLI_TELEMETRY = "0"; .sam-venv\Scripts\sam ...`.

## Decisions
- **Stored, not live search**: a scheduled fetch writes to storage; MCP tools only read.
  Comparison: https://claude.ai/artifact/2cvKQniyppBmGSaHNfsuCn
- **Fetch every 15 minutes** (user's choice; ~1,250 feed requests/day, still €0).
- **MCP SDK v2** (`MCPServer`), because v2 renamed FastMCP.
- **"Same event, different angle" stays separate**: no wrong merges beats catching every match.
- **NewsAPI left out** (free plan is development-only). **Reddit on hold.**

## Next
1. **Deploy** (needs approval). Before: `aws sts get-caller-identity` (right account?) and
   Billing → Free Tier page (accounts created after 15 Jul 2025 have different terms).
   Then `sam build` and `.sam-venv\Scripts\sam deploy --guided` with: stack `cloud-ai-news`,
   region `eu-west-1`, AlertEmail = your email or empty, confirm changes **Y**, IAM role
   creation **Y**, disable rollback **N**, save arguments **n** (keeps the email out of git).
   Approve the changeset only if it adds exactly: NewsTable, FetcherLogGroup, FetcherRole,
   FetcherFunction, FetcherFunctionEvery15Minutes, FetcherFunctionEvery15MinutesRole
   (+ MonthlyBudget).
2. After deploy: `aws lambda invoke --function-name cloud-ai-news-fetcher --region eu-west-1
   out.json`, then `aws logs tail /aws/lambda/cloud-ai-news-fetcher --region eu-west-1`.
   Check the Free Tier usage page a day later.
3. Run server.py on AWS (Streamable HTTP + Lambda function URL) reading DynamoDB.

## Open problems
- **Cost flags**: SAM's managed S3 bucket for the code zip isn't always-free (fractions of a cent
  per month); add a lifecycle rule. DynamoDB free capacity is account-wide. First run may be
  throttled at 1 WCU (time, not money).
- If you ran `claude mcp add` before the `app/` move: `claude mcp remove cloud-ai-news`, then
  re-add with the path in CLAUDE.md (`app\server.py`).
- **Reddit (r/aws, r/LocalLLaMA)**: needs a Reddit API app (OAuth). On AWS use SSM Parameter
  Store (free), not Secrets Manager (~$0.40/secret/month).
- **Aggregators**: Hacker News and Techmeme often just link to the original, inflating counts.
- **Google News redirect links** (VentureBeat): resolve to the real URL.
- **Story titles** keep prefixes like "Sources: …"; clean before reels.
- DynamoDB edge case: if a feed changes an article's date to another day, the article could be
  stored twice (its key includes the date). SQLite isn't affected.
- An article matching two stored stories joins the better one; the stories aren't merged.

## Source workarounds (check these still work)
- **Anthropic**: third-party `Olshansk/rss-feeds` GitHub feed; fallback `anthropic.com/sitemap.xml`.
- **Meta AI**: Engineering at Meta, AI Research category (`ai.meta.com` has no feed).
- **VentureBeat**: Google News search feed (VentureBeat returns 429 to automated readers).
