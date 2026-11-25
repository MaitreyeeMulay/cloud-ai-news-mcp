"""Tests for lambda_handler.py on a moto-faked DynamoDB table; no network, no real AWS."""

from datetime import timedelta

import pytest
from moto import mock_aws

import fetcher
import lambda_handler
import storage
import storage_dynamodb
from tests.pairs import NOT_SAME, SAME, T


@pytest.fixture
def fake_aws(monkeypatch):
    """Configure the handler like template.yaml does, against a fake table."""
    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")
    monkeypatch.setenv("TABLE_NAME", "news-test")
    with mock_aws():
        storage_dynamodb.create_table("news-test")
        yield


def test_handler_saves_to_dynamodb_and_reruns_safely(fake_aws, monkeypatch):
    # Replace the network fetch with the 10 saved headlines.
    articles = [x for pair in SAME + NOT_SAME for x in pair.values]
    monkeypatch.setattr(fetcher, "fetch_recent", lambda feeds=None, now=None: articles)

    first = lambda_handler.handler({}, None)
    second = lambda_handler.handler({}, None)

    assert first == {"new_articles": 10, "new_stories": 7, "skipped": 0}
    assert second == {"new_articles": 0, "new_stories": 0, "skipped": 10}
    with storage.connect() as store:
        stories = store.get_stories(since=T - timedelta(days=1))
    assert [s.source_count for s in stories] == [2, 2, 2, 1, 1, 1, 1]
