# Cloud + AI News-to-Reels MCP Server

<!--
HOW THIS FILE WORKS
Claude Code reads this file at the start of every session. Think of it as a
standing prompt: your project's goal and rules, written once instead of
repeated in every message.
Keep it short and specific. Comment blocks like this one are notes for you.
You can delete them once you understand each section.
-->

## Goal
<!--
GOAL: 1-2 sentences on WHAT you're building and WHO it's for.
Claude uses this to make sensible choices you didn't spell out.
Vague: "A news app."  Specific: what it does, the output, the constraint.
-->
A public MCP server that collects cloud and AI news from RSS feeds and APIs,
counts how many independent sources report each story, and turns the top
stories into 15-30 second reel scripts with a link to the full story.
It must run on the AWS always-free tier (target cost: €0).

## Stack
<!--
STACK: the exact languages, libraries and services to use.
Without this, Claude may pick different ones each session.
Name versions where they matter. Add to it as the project grows.
-->
- Python 3.11+, virtual environment, dependencies in requirements.txt
- MCP Python SDK v2 for the server: `from mcp.server.mcpserver import MCPServer`
  (v2 renamed FastMCP; `mcp.server.fastmcp` no longer exists)
- feedparser + httpx for fetching feeds
- pytest for tests
- Local storage: SQLite
- AWS (region eu-west-1): Lambda, EventBridge Scheduler, DynamoDB, Lambda
  function URL, deployed with AWS SAM
- MCP transport: stdio locally, Streamable HTTP when deployed

## Rules
<!--
RULES: your non-negotiables. This is the most important section.
Write each rule as a short, direct instruction, not an explanation.
When Claude gets something wrong, add a rule so it doesn't happen again.
This is prompt engineering in practice: small, specific, testable instructions.
-->
- Explain your plan before editing files. Wait for my approval on big changes.
- One task at a time. Keep functions small, with type hints and docstrings.
- Add comments to every script explaining what each step does.
- Write pytest tests for new logic. Never call live feeds or AWS in tests;
  use saved sample files and mocks.
- Never put secrets, keys or account IDs in code. Use environment variables.
- Stay within the AWS always-free tier. Flag anything that could cost money
  before creating it.
- DynamoDB must use PROVISIONED mode, 1 RCU / 1 WCU. Never on-demand.
- Use least-privilege IAM: only the actions the code actually uses.
- Never run commands that delete AWS resources without asking me first.
- Write MCP tool descriptions as if Claude must choose the right tool from
  the description alone.
- I'm learning MCP and prompt engineering: briefly explain new concepts
  when you use them.

## Commands
<!--
COMMANDS: how to set up, run, test and deploy the project.
Claude uses these instead of guessing. Fill them in as each one starts
working. Wrong commands here cause wrong actions, so keep them up to date.
-->
- Set up:        `python -m venv .venv` then `.venv\Scripts\activate` (Windows)
                 then `pip install -r requirements.txt`
- Run fetcher:   `python fetcher.py`
- Run tests:     `pytest`
- Test server:   `npx @modelcontextprotocol/inspector python server.py`
- Build/deploy:  `sam build` then `sam deploy` (ask me before deploying)


## Progress
<!--
PROGRESS: point to a separate file instead of logging progress here.
At the end of each sitting, ask Claude Code to update PROGRESS.md.
This keeps CLAUDE.md short and lets each sitting pick up where you left off.
-->
Read PROGRESS.md at the start of each session. Update it at the end with
what was done, what's next, and any open problems.
