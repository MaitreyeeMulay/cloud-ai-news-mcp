"""DynamoDB storage backend, for AWS. See storage.py for the interface.

One table, two kinds of item, no indexes. Every read is a Query on one day's
partition (never a Scan), which keeps the table within 1 RCU / 1 WCU:

  Article  PK = "ADAY#<published date>"   SK = "SRC#<source>#<link_key>"
  Story    PK = "SDAY#<first_seen date>"  SK = "STORY#<story id>"

The IAM policy allows exactly the calls used here: Query, PutItem, UpdateItem.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from models import Article, Story

# Items delete themselves this long after they're written (DynamoDB TTL, free).
RETENTION = timedelta(days=30)

# At 1 WCU, writes get throttled in bursts; back off and retry instead of failing.
RETRY_CONFIG = Config(retries={"mode": "standard", "max_attempts": 10})


def _iso(dt: datetime) -> str:
    """Store datetimes as UTC ISO strings, which also sort correctly as text."""
    return dt.astimezone(UTC).isoformat()


def _days(since: datetime, until: datetime) -> Iterator[date]:
    """Every UTC calendar date from `since` to `until`, inclusive."""
    day, last = since.astimezone(UTC).date(), until.astimezone(UTC).date()
    while day <= last:
        yield day
        day += timedelta(days=1)


def _expires_at(dt: datetime) -> int:
    """TTL attribute: Unix time (seconds) after which DynamoDB may delete the item."""
    return int((dt + RETENTION).timestamp())


def story_key(story_id: str) -> dict[str, str]:
    """Primary key of a story item. The id starts with first_seen (yyyymmdd...)."""
    day = f"{story_id[0:4]}-{story_id[4:6]}-{story_id[6:8]}"
    return {"PK": f"SDAY#{day}", "SK": f"STORY#{story_id}"}


class DynamoBackend:
    """storage.Backend on a DynamoDB table."""

    def __init__(self, table_name: str) -> None:
        # Region and credentials come from the environment (set by Lambda on AWS).
        self.table = boto3.resource("dynamodb", config=RETRY_CONFIG).Table(table_name)

    def _query_partition(self, pk: str) -> list[dict]:
        """All items in one partition, following pagination (1 MB per page)."""
        items, kwargs = [], {"KeyConditionExpression": Key("PK").eq(pk)}
        while True:
            page = self.table.query(**kwargs)
            items.extend(page["Items"])
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def load_articles_since(self, since: datetime, until: datetime) -> list[tuple[str, Article]]:
        """Query each article day partition in range, then trim to [since, until]."""
        result = []
        for day in _days(since, until):
            for item in self._query_partition(f"ADAY#{day.isoformat()}"):
                published = datetime.fromisoformat(item["published"])
                if since <= published <= until:
                    result.append((item["story_id"], Article(
                        title=item["title"], link=item["link"], source=item["source"],
                        category=item["category"], published=published)))
        return result

    def add_article(self, story_id: str, article: Article, link_key: str) -> bool:
        """Put one article item; the condition makes a duplicate a no-op (returns False)."""
        try:
            self.table.put_item(
                Item={
                    "PK": f"ADAY#{article.published.astimezone(UTC).date().isoformat()}",
                    "SK": f"SRC#{article.source}#{link_key}",
                    "story_id": story_id,
                    "source": article.source,
                    "link": article.link,
                    "title": article.title,
                    "category": article.category,
                    "published": _iso(article.published),
                    "expires_at": _expires_at(article.published),
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def create_story(self, story_id: str, article: Article, now: datetime) -> None:
        """Put a story item with this article as its only source."""
        item = {
            **story_key(story_id),
            "id": story_id,
            "title": article.title,
            "category": article.category,
            "first_seen": _iso(now),
            "source_count": 1,
            "sources": [{"source": article.source, "link": article.link,
                         "published": _iso(article.published)}],
            "expires_at": _expires_at(now),
        }
        # Leave summary out when empty, so update_story's if_not_exists can fill it later.
        if article.summary:
            item["summary"] = article.summary
        self.table.put_item(Item=item)

    def update_story(self, story_id: str, article: Article, source_count: int) -> None:
        """One UpdateItem: append the source, set the count, fill a missing summary."""
        expression = "SET sources = list_append(sources, :new), source_count = :count"
        values = {
            ":new": [{"source": article.source, "link": article.link,
                      "published": _iso(article.published)}],
            ":count": source_count,
        }
        if article.summary:
            expression += ", summary = if_not_exists(summary, :summary)"
            values[":summary"] = article.summary
        self.table.update_item(Key=story_key(story_id), UpdateExpression=expression,
                               ExpressionAttributeValues=values)

    def get_stories(self, since: datetime, until: datetime, limit: int) -> list[Story]:
        """Query each story day partition in range, then rank in code."""
        stories = []
        for day in _days(since, until):
            for item in self._query_partition(f"SDAY#{day.isoformat()}"):
                first_seen = datetime.fromisoformat(item["first_seen"])
                if not since <= first_seen <= until:
                    continue
                # Sources oldest first, like the SQLite backend.
                sources = sorted(item["sources"], key=lambda s: s["published"])
                stories.append(Story(
                    id=item["id"], title=item["title"], summary=item.get("summary", ""),
                    category=item["category"], first_seen=first_seen,
                    source_count=int(item["source_count"]),  # boto3 returns Decimal
                    sources=tuple((s["source"], s["link"]) for s in sources),
                ))
        # Same order as SQLite: most sources, then newest, then id.
        stories.sort(key=lambda s: (s.source_count, s.first_seen, s.id), reverse=True)
        return stories[:limit]


def create_table(table_name: str) -> None:
    """Create the table with the same keys and capacity as template.yaml.
    Used by tests (against moto's fake DynamoDB); on AWS the SAM template creates it."""
    boto3.client("dynamodb").create_table(
        TableName=table_name,
        AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                              {"AttributeName": "SK", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"},
                   {"AttributeName": "SK", "KeyType": "RANGE"}],
        BillingMode="PROVISIONED",
        ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
    )
