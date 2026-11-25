"""Checks the lifecycle rules for SAM's upload bucket (infra/sam-bucket-lifecycle.json).

Pure file checks: no AWS calls. They guard the day counts we agreed on and the
shape the S3 API needs (one rule per Expiration kind, an explicit Filter).
"""

import json
from pathlib import Path

import pytest

RULES_FILE = Path(__file__).resolve().parent.parent / "infra" / "sam-bucket-lifecycle.json"


@pytest.fixture(scope="module")
def rules() -> dict[str, dict]:
    """Load the rules file and index the rules by ID."""
    config = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    return {rule["ID"]: rule for rule in config["Rules"]}


def test_every_rule_is_enabled_and_covers_the_whole_bucket(rules):
    """A rule without a Filter, or one left disabled, would silently do nothing useful."""
    assert rules, "no rules found"
    for rule in rules.values():
        assert rule["Status"] == "Enabled"
        assert rule["Filter"] == {"Prefix": ""}


def test_uploads_expire_after_90_days_and_leftovers_after_1(rules):
    """90 days for current uploads; old versions and broken uploads go after 1 day."""
    main = rules["expire-old-deploy-artifacts"]
    assert main["Expiration"] == {"Days": 90}
    assert main["NoncurrentVersionExpiration"] == {"NoncurrentDays": 1}
    assert main["AbortIncompleteMultipartUpload"] == {"DaysAfterInitiation": 1}


def test_delete_markers_are_cleaned_in_their_own_rule(rules):
    """S3 rejects ExpiredObjectDeleteMarker in the same rule as Expiration Days."""
    markers = rules["remove-expired-delete-markers"]
    assert markers["Expiration"] == {"ExpiredObjectDeleteMarker": True}
    assert "Days" not in markers["Expiration"]
