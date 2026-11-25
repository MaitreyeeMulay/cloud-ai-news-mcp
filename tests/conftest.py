"""Shared fixtures: run storage tests against both backends, never real AWS."""

import pytest
from moto import mock_aws

import storage
import storage_dynamodb

TABLE = "news-test"


@pytest.fixture(autouse=True)
def no_real_aws(monkeypatch):
    """Fake credentials and region for every test, so nothing can reach a real account."""
    for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                        "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "eu-west-1"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture(params=["sqlite", "dynamodb"])
def backend_env(request, tmp_path, monkeypatch):
    """Point storage.connect() at an empty temp SQLite file or a fake (moto) DynamoDB table."""
    monkeypatch.setenv("STORAGE_BACKEND", request.param)
    if request.param == "sqlite":
        monkeypatch.setenv("NEWS_DB", str(tmp_path / "news.db"))
        yield request.param
    else:
        monkeypatch.setenv("TABLE_NAME", TABLE)
        with mock_aws():
            storage_dynamodb.create_table(TABLE)
            yield request.param


@pytest.fixture
def store(backend_env):
    """An open Store on the backend chosen by backend_env."""
    with storage.connect() as s:
        yield s
