# Plan: run server.py on Lambda as a remote MCP server

**Status: draft, NOT approved, nothing built.** Written at the end of Sitting 4 (2026-11-25).
Review this, then approve or change it before any code or AWS resource is created.

## Goal (from the Sitting 4 request)
- Streamable HTTP transport, stateless, JSON responses
- A Lambda function URL with auth type NONE, so any MCP client can connect
- Reads DynamoDB only (read-only IAM)
- Guardrails: reserved concurrency of 2-5, input length limits on every tool, max 20 results per call

## Choice: AWS Lambda Web Adapter (LWA), not Mangum

**What they are.** Lambda hands your code one *event* per request, but the MCP SDK builds a
normal web app (ASGI/Starlette) that expects HTTP. Something has to translate:
- **Mangum** is a Python library: a `handler(event, context)` function converts each event into
  an ASGI call inside the same Python process.
- **Lambda Web Adapter** is an AWS-maintained Lambda *layer* (a ready-made add-on). It starts
  your app as a normal web server (uvicorn on port 8080) and forwards each Lambda event to it as
  a real HTTP request.

**Why LWA:**
1. **It matches what the SDK requires.** The SDK docs (`docs/run/asgi.md`) say the app must
   enter `mcp.session_manager.run()` in its lifespan, and `docs/whats-new.md` says that lifespan
   "runs once, at startup". With LWA, uvicorn runs the lifespan once per execution environment,
   exactly as designed. Mangum's docs don't say whether lifespan runs once or per invocation; its
   code appears to enter it on every invocation, and its README example uses `lifespan="off"`,
   which would break the SDK ("Task group is not initialized").
2. **The same app runs locally unchanged**: `uvicorn http_app:app --port 8080`, so the MCP
   Inspector and Claude Code can test the exact thing that ships.
3. **AWS-maintained**, supports function URLs, and the layer adds no charge (layers are free;
   it only counts toward the package size).

**Costs of the choice:** a shell start script (`run.sh`, must have LF line endings and be
executable in the zip built on Windows), an extra process per cold start (slightly slower), and
a pinned layer version to keep up to date.

Neither option is mentioned in the official MCP SDK docs; both are third-party to the SDK.

## Design

### Code (`app/`)
- **`app/http_app.py`** (new): builds the ASGI app from the existing `mcp` object:
  `mcp.streamable_http_app(stateless_http=True, json_response=True, max_request_body_size=<small, e.g. 16 KB>, transport_security=...)`.
  - `stateless_http=True`: a fresh transport per request, no session ID to keep. Needed because
    a function URL has no "sticky sessions" (requests land on any execution environment).
  - `json_response=True`: one JSON body per POST instead of an SSE stream. Trade-off from the SDK
    docs: no progress notifications, sampling or elicitation. Our tool needs none of them.
  - **Host check**: by default the SDK accepts only `localhost`/`127.0.0.1` Host headers and
    returns 421 for anything else, so a function URL (`*.lambda-url.eu-west-1.on.aws`) would be
    rejected. Plan: `TransportSecuritySettings(enable_dns_rebinding_protection=False)`. DNS
    rebinding protection guards servers on a user's own machine; this server is public with no
    cookies or credentials to steal. (The URL's host can't be put in an env var on the same
    function: CloudFormation would see a circular dependency.)
- **`app/run.sh`** (new): LWA start script, `exec python -m uvicorn http_app:app --host 127.0.0.1 --port 8080`
  (no access log, to keep CloudWatch small).
- **`app/server.py`**: tighten inputs with schema limits so they are *advertised to the client*,
  not only clamped silently:
  - `count: Annotated[int, Field(ge=1, le=20)]`, `hours: Annotated[int, Field(ge=1, le=168)]`,
    `category: Literal["cloud","ai","tech"] | None` (already limited).
  - Same `count` limit on the `reel_scripts` prompt.
  - Keep the existing clamp as a second line of defence; max 20 results stays.
- **In-memory cache (60 s)** for `get_stories` results per execution environment: with 1 RCU,
  even modest traffic would be throttled; repeated calls should not hit DynamoDB.
- **Dependencies**: `app/requirements.txt` gains `mcp` and `uvicorn`. Both functions share
  `CodeUri: app/`, so the fetcher zip grows too (simple; acceptable).

### Template (`template.yaml`)
- **`McpServerFunction`**: Python 3.12, x86_64, 256 MB, timeout 10 s, handler `run.sh`,
  layer `arn:aws:lambda:eu-west-1:753240598075:layer:LambdaAdapterLayerX86:<pinned version>`,
  env `AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap`, `AWS_LWA_PORT=8080`, `STORAGE_BACKEND=dynamodb`,
  `TABLE_NAME`. `ReservedConcurrentExecutions: 2`.
- **Function URL**: `AuthType: NONE`, invoke mode BUFFERED (JSON responses need no streaming).
  Output the URL; MCP endpoint is `<url>/mcp`.
- **`McpServerRole`** (explicit, least privilege): `dynamodb:Query` on the table only;
  `logs:CreateLogStream`, `logs:PutLogEvents` on its own log group only. No write actions.
- **`McpServerLogGroup`**: 7-day retention.
- Optional: one CloudWatch alarm on invocations per day → email (10 alarms are always-free).

### Tests (no network, no AWS)
- Starlette `TestClient` against `http_app.app` with moto DynamoDB: `initialize`, `tools/list`
  (schema shows the 1-20 / 1-168 limits), `tools/call`, `count=21` rejected, request with a
  `*.lambda-url.eu-west-1.on.aws` Host header accepted, oversize body rejected.
- `run.sh` has LF line endings (add `.gitattributes`: `*.sh text eol=lf`).

### Deploy and verify
- Two-step deploy as in PROGRESS.md (changeset reviewed before execution).
- Then: MCP Inspector `--transport http` against `<url>/mcp`, and
  `claude mcp add --transport http cloud-ai-news-remote <url>/mcp`.

## Cost and risk flags (read before approving)
- **Public URL, no auth = anyone can call it.** Reserved concurrency 2 caps parallel runs, not
  total calls. Worst case, sustained abuse at ~20 requests/s could pass the 1M free requests per
  month (then ~$0.20 per extra million, plus compute). The $1 budget email is the backstop; the
  fix would be setting reserved concurrency to 0 (pauses the function, deletes nothing).
- **Reserved concurrency may be rejected**: AWS requires at least 10 unreserved concurrent
  executions; new accounts can have a total limit of 10, which leaves nothing to reserve.
  Check `aws lambda get-account-settings` first (not checked yet).
- **Function URL permissions**: newer AWS rules may require both `lambda:InvokeFunctionUrl` and
  `lambda:InvokeFunction` in the URL's resource policy. Confirm what SAM 1.174 generates.
- **Building on Windows**: `pydantic-core` (pulled in by `mcp`) is compiled; `sam build` must
  fetch the Linux (manylinux x86_64) wheel, and `run.sh` must keep its execute bit in the zip.
  Verify both after `sam build`, before deploying.

## Not yet verified (checks that were interrupted)
1. Account concurrency limit (`aws lambda get-account-settings --region eu-west-1`).
2. How SAM sets file permissions in zips built on Windows (for `run.sh`).
3. That the DynamoDB read path uses only `Query` (PROGRESS.md says so; confirm in
   `app/storage_dynamodb.py`).
4. Current LWA layer version to pin (README showed `LambdaAdapterLayerX86:30`).

## Sources
- MCP Python SDK docs: `docs/run/index.md`, `docs/run/asgi.md`, `docs/run/deploy.md`,
  `docs/whats-new.md` (github.com/modelcontextprotocol/python-sdk). Installed SDK: mcp 2.3.0.
- github.com/awslabs/aws-lambda-web-adapter (README)
- github.com/Kludex/mangum (README, docs/lifespan.md)
