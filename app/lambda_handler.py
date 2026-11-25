"""AWS Lambda entry point: EventBridge Scheduler calls handler() every 15 minutes.

The template sets STORAGE_BACKEND=dynamodb and TABLE_NAME, so fetcher.run()
saves into DynamoDB. A run that times out is harmless: saving is idempotent,
so the next run stores whatever this one didn't reach.
"""

import logging
from dataclasses import asdict

import fetcher

# Lambda's runtime already sends the root logger to CloudWatch; just set the level.
logging.getLogger().setLevel(logging.INFO)
# One log line per HTTP request would bloat the logs; keep only httpx warnings.
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger(__name__)


def handler(event: dict, context: object) -> dict:
    """Fetch all feeds, de-duplicate, save. Returns the counts (also logged)."""
    result = fetcher.run()
    log.info("Saved %d new articles (%d new stories, %d already stored)",
             result.new_articles, result.new_stories, result.skipped)
    return asdict(result)
