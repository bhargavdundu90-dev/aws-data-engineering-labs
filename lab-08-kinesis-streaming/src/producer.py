"""Kinesis producer for UPI/card transactions.

- Partition key = account_id, so all events of one account go to the same shard and stay
  in order (a fraud rule like "3 debits in 60 seconds" needs per-account order).
- PutRecords in batches of up to 500 records / 5 MB (the API limits).
- PutRecords is not all-or-nothing: the call succeeds while some records fail with
  ProvisionedThroughputExceededException. Only the failed records are retried, with
  exponential backoff and jitter, and the caller gets back what still failed.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

MAX_RECORDS = 500
MAX_BATCH_BYTES = 5 * 1024 * 1024
MAX_RECORD_BYTES = 1024 * 1024


@dataclass
class PutResult:
    sent: int = 0
    attempts: int = 0
    retried: int = 0
    failed: list = field(default_factory=list)   # records that never made it, with the last error


def make_transactions(n: int, seed: int = 7, accounts: int = 20, start: datetime | None = None) -> list[dict]:
    rnd = random.Random(seed)
    start = start or datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
    out, ts = [], start
    for i in range(n):
        ts += timedelta(seconds=rnd.randint(1, 4))
        out.append({
            "txn_id": f"T{seed:02d}{i:06d}",
            "account_id": f"ACC{rnd.randint(1, accounts):04d}",
            "channel": rnd.choice(["UPI", "UPI", "CARD", "NEFT"]),
            "amount": round(rnd.lognormvariate(7, 1.2), 2),
            "currency": "INR",
            "merchant_city": rnd.choice(["Hyderabad", "Bengaluru", "Pune", "Chennai", "Mumbai"]),
            "event_ts": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        })
    return out


def to_entry(txn: dict) -> dict:
    data = json.dumps(txn, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError(f"{txn.get('txn_id')} is larger than the 1 MB Kinesis record limit")
    return {"Data": data, "PartitionKey": txn["account_id"]}


def batches(entries: list[dict]):
    """Split into PutRecords calls by record count and payload size (data + partition key)."""
    batch, size = [], 0
    for e in entries:
        e_size = len(e["Data"]) + len(e["PartitionKey"].encode())
        if batch and (len(batch) == MAX_RECORDS or size + e_size > MAX_BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(e)
        size += e_size
    if batch:
        yield batch


def put_with_retry(client, stream: str, entries: list[dict], max_attempts: int = 5,
                   base_delay: float = 0.1, sleep=time.sleep) -> PutResult:
    res = PutResult()
    pending = entries
    for attempt in range(1, max_attempts + 1):
        res.attempts += 1
        resp = client.put_records(StreamName=stream, Records=pending)
        failed = [(e, r) for e, r in zip(pending, resp["Records"]) if "ErrorCode" in r]
        res.sent += len(pending) - len(failed)
        if not failed:
            return res
        if attempt == max_attempts:
            res.failed = [{"entry": e, "error": r["ErrorCode"]} for e, r in failed]
            return res
        res.retried += len(failed)
        pending = [e for e, _ in failed]
        sleep(base_delay * (2 ** (attempt - 1)) * (0.5 + random.random()))
    return res


def send(client, stream: str, txns: list[dict], **kw) -> PutResult:
    total = PutResult()
    for b in batches([to_entry(t) for t in txns]):
        r = put_with_retry(client, stream, b, **kw)
        total.sent += r.sent
        total.attempts += r.attempts
        total.retried += r.retried
        total.failed += r.failed
    return total


def shards_needed(records_per_sec: float, avg_record_kb: float, consumers: int = 1,
                  enhanced_fanout: bool = False) -> int:
    """Provisioned-mode sizing: 1 MB/s or 1,000 records/s in per shard, 2 MB/s out shared by
    standard consumers (enhanced fan-out gives each consumer its own 2 MB/s)."""
    in_mb = records_per_sec * avg_record_kb / 1024
    by_in_bytes = in_mb / 1.0
    by_in_records = records_per_sec / 1000
    out_consumers = 1 if enhanced_fanout else consumers
    by_out = in_mb * out_consumers / 2.0
    need = max(by_in_bytes, by_in_records, by_out)
    return max(1, int(-(-need // 1)))   # ceil


if __name__ == "__main__":
    import argparse
    import boto3

    p = argparse.ArgumentParser(description="Send sample transactions to a Kinesis stream")
    p.add_argument("--stream", required=True)
    p.add_argument("--count", type=int, default=1000)
    a = p.parse_args()
    r = send(boto3.client("kinesis"), a.stream, make_transactions(a.count))
    print(f"sent={r.sent} retried={r.retried} failed={len(r.failed)} calls={r.attempts}")
