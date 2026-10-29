import json
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "src"))
import handler  # noqa: E402

BUCKET = "bfsi-landing-dev"


@pytest.fixture
def s3():
    os.environ.update(AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test", AWS_REGION="ap-south-1")
    with mock_aws():
        handler._s3 = None
        client = boto3.client("s3", region_name="ap-south-1")
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        yield client
        handler._s3 = None


def _event(key):
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}


def _read_jsonl(client, key):
    body = client.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    return [json.loads(line) for line in body.splitlines()]


def test_validate_row_normalises_currency_and_amount():
    row, reason = handler.validate_row(
        {"txn_id": "T1", "account_id": "A", "txn_ts": "2026-10-29T10:00:00", "amount": "120.5", "currency": "usd"}
    )
    assert reason is None
    assert row["currency"] == "USD" and row["amount"] == "120.50" and row["txn_date"] == "2026-10-29"


@pytest.mark.parametrize(
    "field,value,reason",
    [("amount", "-1", "negative amount"), ("amount", "x", "amount not numeric"),
     ("currency", "JPY", "unknown currency JPY"), ("account_id", "", "missing account_id"),
     ("txn_ts", "29/10/2026", "bad txn_ts")],
)
def test_validate_row_rejects(field, value, reason):
    row = {"txn_id": "T1", "account_id": "A", "txn_ts": "2026-10-29T10:00:00", "amount": "1", "currency": "INR"}
    row[field] = value
    assert handler.validate_row(row) == (None, reason)


def test_handler_splits_valid_and_quarantine(s3):
    key = "raw/transactions/txns_2026-10-29.csv"
    s3.put_object(Bucket=BUCKET, Key=key, Body=(LAB / "sample_data/txns_2026-10-29.csv").read_bytes())

    result = handler.lambda_handler(_event(key), None)

    assert result["processed"][0] == {"key": key, "valid": 3, "rejected": 5}
    good = _read_jsonl(s3, "curated/transactions/txns_2026-10-29.jsonl")
    assert [r["txn_id"] for r in good] == ["T1001", "T1002", "T1008"]
    reasons = {r["reason"] for r in _read_jsonl(s3, "quarantine/transactions/txns_2026-10-29.rejects.jsonl")}
    assert "duplicate txn_id in file" in reasons and "negative amount" in reasons


def test_handler_is_idempotent_on_retry(s3):
    key = "raw/transactions/txns_2026-10-29.csv"
    s3.put_object(Bucket=BUCKET, Key=key, Body=(LAB / "sample_data/txns_2026-10-29.csv").read_bytes())
    handler.lambda_handler(_event(key), None)
    handler.lambda_handler(_event(key), None)  # S3 can deliver the same event twice
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix="curated/")["Contents"]]
    assert keys == ["curated/transactions/txns_2026-10-29.jsonl"]


def test_url_encoded_key_and_non_csv_skip(s3):
    s3.put_object(Bucket=BUCKET, Key="raw/transactions/day 1.csv",
                  Body=b"txn_id,account_id,txn_ts,amount,currency\nT9,A9,2026-10-29T08:00:00,10,INR\n")
    out = handler.lambda_handler(_event("raw/transactions/day+1.csv"), None)
    assert out["processed"][0]["valid"] == 1
    out = handler.lambda_handler(_event("raw/transactions/readme.txt"), None)
    assert out["processed"][0]["skipped"] == "not a csv"


def test_missing_header_column_fails_loudly(s3):
    s3.put_object(Bucket=BUCKET, Key="raw/transactions/bad.csv", Body=b"txn_id,amount\nT1,10\n")
    with pytest.raises(ValueError, match="header missing columns"):
        handler.lambda_handler(_event("raw/transactions/bad.csv"), None)
