# Progress

## Status (Sitting 4, 2026-11-25)
**Fetcher deployed to AWS (eu-west-1) and running every 15 minutes.** Stack `cloud-ai-news`
is CREATE_COMPLETE (7 resources, exactly the planned changeset). First runs saved ~74 articles;
a run takes ~21 s and 103 MB (limits 60 s / 256 MB). FetcherRole verified: no managed policies,
only Query/PutItem/UpdateItem on the table and CreateLogStream/PutLogEvents on its log group.
S3 lifecycle rule applied to SAM's bucket and read back. The MCP server still runs locally only.

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
- **S3 lifecycle rule** for SAM's managed upload bucket (lives in SAM's own stack
  `aws-sam-cli-managed-default`, versioned, so it can't go in template.yaml):
  `infra/sam-bucket-lifecycle.json` (uploads expire after 90 days, old versions and broken
  uploads after 1 day, delete markers cleaned) + `scripts/apply-sam-bucket-lifecycle.ps1`
  (finds the bucket, shows the current config, asks, applies, reads back). Shared by every SAM
  project in eu-west-1; re-run if SAM ever recreates that stack.
- **Checks**: 60 tests pass (storage tests on SQLite and moto-faked DynamoDB; Lambda handler
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

## Finished in Sitting 4 (2026-11-25)
- S3 lifecycle rule (file + script + tests), committed.
- AWS access via Identity Center (profile `tracker`); account checked: it's the organization's
  management account, created 7 Jul 2025 (old free-tier terms; 12-month offers have expired, so
  only always-free applies; S3 is the only paid item, ~€0.00002/month).
- Fetcher deployed (stack `cloud-ai-news`, 7 resources), verified (IAM role read back, invoke
  OK, logs OK), lifecycle rule applied to SAM's bucket.
- Researched how to run the MCP server on Lambda; draft plan saved to `docs/plan-sitting-4.md`
  (chosen: AWS Lambda Web Adapter, not Mangum). **Not approved yet, nothing built.**
- Paused here. Still running in AWS: the fetcher every 15 min (inside always-free).

## Next
**Sitting 5, prompt 1:** "Read PROGRESS.md and docs/plan-sitting-4.md. Run the 4 checks under
'Not yet verified' in the plan (read-only), update the plan with the results, then wait for my
approval before writing code."
After approval: build the remote MCP server as planned (code + tests, then template, then
`sam build` checks, then two-step deploy).
Also, any time: check Billing → Free Tier usage, and confirm the $1 budget subscription email.

## How to deploy (what worked)
- AWS access: Identity Center user, keys pasted from the access portal (Access keys →
  Option 2) into the local credentials file under profile `tracker`; they expire ~hourly.
  In each PowerShell window: `$env:AWS_PROFILE = "tracker"; $env:SAM_CLI_TELEMETRY = "0"`.
- Avoid `sam deploy --guided` (if used, answer "Save arguments" with n, else the email lands in
  samconfig.toml). Instead deploy in two steps, so the changeset can be reviewed first:
  `.sam-venv\Scripts\sam build`, then
  `.sam-venv\Scripts\sam deploy --no-execute-changeset --no-confirm-changeset --parameter-overrides AlertEmail=<email>`
  then, after review, `aws cloudformation execute-change-set --change-set-name <arn> --region eu-west-1`.
- Checks: `aws lambda invoke --function-name cloud-ai-news-fetcher --region eu-west-1 out.json`;
  `aws logs tail /aws/lambda/cloud-ai-news-fetcher --region eu-west-1 --since 1h`.

## Open problems
- **Cost flags**: SAM's managed S3 bucket isn't always-free (fractions of a cent per month);
  capped by the lifecycle rule (applied 2026-11-25). DynamoDB free capacity is account-wide.
- **Rollback risk from the 90-day rule**: if a deploy fails > 90 days after the previous one,
  the old code zip is gone and the stack can end in UPDATE_ROLLBACK_FAILED; recover with
  `aws cloudformation continue-update-rollback`.
- **Remote MCP server (planned)**: a public function URL with no auth can be called by anyone;
  see the cost and risk flags in `docs/plan-sitting-4.md` before building it. 1 RCU will throttle
  reads under any real traffic (plan adds a 60 s in-memory cache).
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
- **Meta AI feed** failed once on Lambda ("Server disconnected without sending a response");
  skipped as designed. Watch whether it fails every run from AWS IPs.

## Source workarounds (check these still work)
- **Anthropic**: third-party `Olshansk/rss-feeds` GitHub feed; fallback `anthropic.com/sitemap.xml`.
- **Meta AI**: Engineering at Meta, AI Research category (`ai.meta.com` has no feed).
- **VentureBeat**: Google News search feed (VentureBeat returns 429 to automated readers).
