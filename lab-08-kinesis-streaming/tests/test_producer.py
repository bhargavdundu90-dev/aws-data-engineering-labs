import json

import boto3
import pytest
from moto import mock_aws

import producer as p


def test_partition_key_is_account_and_payload_is_compact_json():
    txn = p.make_transactions(1)[0]
    e = p.to_entry(txn)
    assert e["PartitionKey"] == txn["account_id"]
    assert json.loads(e["Data"]) == txn


def test_record_over_1mb_is_refused():
    with pytest.raises(ValueError):
        p.to_entry({"txn_id": "T1", "account_id": "A", "blob": "x" * (1024 * 1024)})


def test_batches_respect_500_records_and_5mb():
    small = [p.to_entry(t) for t in p.make_transactions(1201)]
    assert [len(b) for b in p.batches(small)] == [500, 500, 201]

    big = [{"Data": b"x" * (900 * 1024), "PartitionKey": "A"} for _ in range(12)]
    sizes = [sum(len(e["Data"]) for e in b) for b in p.batches(big)]
    assert all(s <= p.MAX_BATCH_BYTES for s in sizes)
    assert sum(len(b) for b in p.batches(big)) == 12


class FlakyKinesis:
    """Fails the given record indexes on the first call(s), like a hot shard throttling."""

    def __init__(self, fail_rounds):
        self.fail_rounds = list(fail_rounds)   # list of sets of positions to fail per call
        self.calls = []

    def put_records(self, StreamName, Records):
        self.calls.append([json.loads(r["Data"])["txn_id"] for r in Records])
        fail = self.fail_rounds.pop(0) if self.fail_rounds else set()
        return {"FailedRecordCount": len(fail), "Records": [
            {"ErrorCode": "ProvisionedThroughputExceededException", "ErrorMessage": "Rate exceeded"}
            if i in fail else {"SequenceNumber": str(i), "ShardId": "shardId-000000000000"}
            for i in range(len(Records))]}


def test_only_failed_records_are_retried(no_sleep):
    sleep, waits = no_sleep
    entries = [p.to_entry(t) for t in p.make_transactions(10)]
    client = FlakyKinesis([{1, 4, 7}, {0}])
    res = p.put_with_retry(client, "s", entries, sleep=sleep)

    assert res.sent == 10 and not res.failed
    assert res.attempts == 3 and res.retried == 4
    first_retry = client.calls[1]
    assert first_retry == [client.calls[0][i] for i in (1, 4, 7)]
    assert client.calls[2] == [first_retry[0]]
    assert len(waits) == 2 and waits[1] > 0


def test_records_still_failing_after_max_attempts_are_returned(no_sleep):
    sleep, _ = no_sleep
    entries = [p.to_entry(t) for t in p.make_transactions(3)]
    client = FlakyKinesis([{2}, {0}, {0}])
    res = p.put_with_retry(client, "s", entries, max_attempts=3, sleep=sleep)
    assert res.sent == 2
    assert len(res.failed) == 1 and res.failed[0]["error"] == "ProvisionedThroughputExceededException"


@mock_aws
def test_send_to_moto_stream_keeps_per_account_order():
    k = boto3.client("kinesis")
    k.create_stream(StreamName="txns", ShardCount=2)
    txns = p.make_transactions(300)
    res = p.send(k, "txns", txns)
    assert res.sent == 300 and not res.failed

    got = []
    for shard in k.list_shards(StreamName="txns")["Shards"]:
        it = k.get_shard_iterator(StreamName="txns", ShardId=shard["ShardId"],
                                  ShardIteratorType="TRIM_HORIZON")["ShardIterator"]
        got += [json.loads(r["Data"]) for r in k.get_records(ShardIterator=it, Limit=1000)["Records"]]
    assert len(got) == 300
    by_acc = {}
    for t in got:
        by_acc.setdefault(t["account_id"], []).append(t["txn_id"])
    for acc, ids in by_acc.items():
        assert ids == [t["txn_id"] for t in txns if t["account_id"] == acc]


@pytest.mark.parametrize("rps,kb,consumers,efo,expected", [
    (500, 1, 1, False, 1),     # 0.49 MB/s in, under 1,000 rec/s
    (2500, 0.5, 1, False, 3),  # record count is the limit: 2.5 shards
    (3000, 2, 1, False, 6),    # bytes in: 5.86 MB/s
    (1000, 1, 4, False, 2),    # 4 standard consumers share 2 MB/s out
    (1000, 1, 4, True, 1),     # enhanced fan-out: each consumer has its own 2 MB/s
])
def test_shard_sizing(rps, kb, consumers, efo, expected):
    assert p.shards_needed(rps, kb, consumers, efo) == expected
