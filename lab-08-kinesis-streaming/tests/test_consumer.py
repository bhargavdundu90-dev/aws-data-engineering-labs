import base64
import gzip
import json
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

import consumer as c
from producer import make_transactions


def kinesis_event(txns, shard="shardId-000000000001", start_seq=49650000000000000000000000000000000000001,
                  raw=None):
    recs = []
    payloads = [json.dumps(t).encode() for t in txns] + [r for r in (raw or [])]
    for i, data in enumerate(payloads):
        seq = str(start_seq + i)
        recs.append({"eventID": f"{shard}:{seq}", "eventSource": "aws:kinesis",
                     "kinesis": {"partitionKey": "k", "sequenceNumber": seq,
                                 "data": base64.b64encode(data).decode(),
                                 "approximateArrivalTimestamp": 1793955600.123}})
    return {"Records": recs}


def read_gz(s3, key):
    body = s3.get_object(Bucket="lake", Key=key)["Body"].read()
    return [json.loads(x) for x in gzip.decompress(body).decode().splitlines() if x]


@pytest.fixture
def s3(monkeypatch):
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="lake", CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        monkeypatch.setenv("BUCKET", "lake")
        monkeypatch.setattr(c, "_s3", client)
        yield client


def keys(s3, prefix):
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket="lake", Prefix=prefix).get("Contents", []))


@pytest.mark.parametrize("txn,reason", [
    ({"txn_id": "T", "account_id": "A", "amount": 10, "currency": "INR"}, "missing event_ts"),
    ({"txn_id": "T", "account_id": "A", "amount": "ten", "currency": "INR", "event_ts": "2026-11-05T09:00:00Z"},
     "amount not numeric"),
    ({"txn_id": "T", "account_id": "A", "amount": 0, "currency": "INR", "event_ts": "2026-11-05T09:00:00Z"},
     "non-positive amount"),
    ({"txn_id": "T", "account_id": "A", "amount": 5, "currency": "JPY", "event_ts": "2026-11-05T09:00:00Z"},
     "unknown currency JPY"),
    ({"txn_id": "T", "account_id": "A", "amount": 5, "currency": "INR", "event_ts": "yesterday"}, "bad event_ts"),
])
def test_validate_rules(txn, reason):
    assert c.validate(txn) == reason


def test_batch_lands_partitioned_by_hour_with_rejects_and_dedupe(s3):
    txns = make_transactions(40)
    dup = dict(txns[3])
    bad = {**txns[0], "txn_id": "BAD1", "amount": -5}
    event = kinesis_event(txns + [dup, bad], raw=[b"{not json"])

    out = c.lambda_handler(event)
    assert out == {"batchItemFailures": []}

    curated = keys(s3, "curated/")
    assert curated and all("/dt=2026-11-05/hour=" in k for k in curated)
    rows = [r for k in curated for r in read_gz(s3, k)]
    assert sorted(r["txn_id"] for r in rows) == sorted(t["txn_id"] for t in txns)   # duplicate dropped
    rejects = read_gz(s3, keys(s3, "quarantine/")[0])
    assert sorted(r["reason"] for r in rejects) == ["non-positive amount", "not json"]


def test_replayed_batch_overwrites_same_objects(s3):
    event = kinesis_event(make_transactions(30))
    c.lambda_handler(event)
    first = keys(s3, "curated/")
    c.lambda_handler(event)
    assert keys(s3, "curated/") == first


def test_failed_partition_reports_its_first_sequence_number(s3, monkeypatch):
    txns = make_transactions(60, seed=3, start=datetime(2026, 11, 5, 9, 58, tzinfo=timezone.utc))  # 09:xx and 10:xx
    event = kinesis_event(txns)
    parts = sorted({c.partition_of(t) for t in txns})
    assert len(parts) >= 2
    real_put = c._put

    def flaky_put(bucket, key, lines):
        if parts[-1] in key:
            raise RuntimeError("SlowDown")
        real_put(bucket, key, lines)

    monkeypatch.setattr(c, "_put", flaky_put)
    out = c.lambda_handler(event)

    first_in_failed = min((r["kinesis"]["sequenceNumber"] for r, t in zip(event["Records"], txns)
                           if c.partition_of(t) == parts[-1]), key=int)
    assert out == {"batchItemFailures": [{"itemIdentifier": first_in_failed}]}
    assert keys(s3, f"curated/transactions/{parts[0]}/")          # earlier hour landed


def test_empty_event_is_a_no_op(s3):
    assert c.lambda_handler({"Records": []}) == {"batchItemFailures": []}
