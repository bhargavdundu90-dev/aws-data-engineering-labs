"""Lambda consumer: Kinesis event source -> S3 (dt/hour partitions, gzip JSON Lines).

Behaviour:
- Decodes each record, validates it, drops duplicates by txn_id inside the batch
  (Kinesis + Lambda is at-least-once; a retried batch can deliver the same records again).
- Writes valid rows to curated/ and bad rows to quarantine/ with the reason. Object keys are
  built from the shard id and the first sequence number of the batch, so a retried batch
  overwrites the same object instead of creating a duplicate file.
- Partial batch response: if the S3 write for some partition fails, it returns
  batchItemFailures starting at the first record of that partition, so Lambda retries from
  there and does not block the shard on records that already landed. Requires
  FunctionResponseTypes = ["ReportBatchItemFailures"] on the event source mapping.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
from collections import defaultdict
from datetime import datetime

import boto3

REQUIRED = ("txn_id", "account_id", "amount", "currency", "event_ts")
CURRENCIES = {"INR", "USD", "EUR", "GBP"}

_s3 = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def validate(txn: dict) -> str | None:
    for c in REQUIRED:
        if txn.get(c) in (None, ""):
            return f"missing {c}"
    try:
        amount = float(txn["amount"])
    except (TypeError, ValueError):
        return "amount not numeric"
    if amount <= 0:
        return "non-positive amount"
    if str(txn["currency"]).upper() not in CURRENCIES:
        return f"unknown currency {txn['currency']}"
    try:
        datetime.fromisoformat(str(txn["event_ts"]).replace("Z", "+00:00"))
    except ValueError:
        return "bad event_ts"
    return None


def partition_of(txn: dict) -> str:
    ts = datetime.fromisoformat(txn["event_ts"].replace("Z", "+00:00"))
    return f"dt={ts:%Y-%m-%d}/hour={ts:%H}"


def parse_records(records: list[dict]):
    """Returns (rows, rejects); each row keeps the sequence number for partial failure."""
    rows, rejects, seen = [], [], set()
    for r in records:
        seq = r["kinesis"]["sequenceNumber"]
        raw = base64.b64decode(r["kinesis"]["data"])
        try:
            txn = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            rejects.append({"seq": seq, "reason": "not json", "raw": raw.decode("utf-8", "replace")[:200]})
            continue
        reason = validate(txn)
        if reason:
            rejects.append({"seq": seq, "reason": reason, "record": txn})
            continue
        if txn["txn_id"] in seen:
            continue                          # duplicate inside the batch
        seen.add(txn["txn_id"])
        txn["_seq"] = seq
        txn["_arrival_ts"] = r["kinesis"].get("approximateArrivalTimestamp")
        rows.append(txn)
    return rows, rejects


def _put(bucket: str, key: str, lines: list[dict]) -> None:
    body = gzip.compress("\n".join(json.dumps(x, separators=(",", ":")) for x in lines).encode() + b"\n")
    s3().put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/x-ndjson",
                    ContentEncoding="gzip")


def lambda_handler(event, context=None):
    bucket = os.environ["BUCKET"]
    records = event.get("Records", [])
    if not records:
        return {"batchItemFailures": []}
    shard = records[0]["eventID"].split(":")[0]
    first_seq = min((r["kinesis"]["sequenceNumber"] for r in records), key=int)

    rows, rejects = parse_records(records)
    by_part = defaultdict(list)
    for row in rows:
        by_part[partition_of(row)].append(row)

    failed_seqs = []
    for part, part_rows in sorted(by_part.items()):
        part_first = min((r["_seq"] for r in part_rows), key=int)
        key = f"curated/transactions/{part}/{shard}-{part_first}.jsonl.gz"
        try:
            _put(bucket, key, part_rows)
        except Exception as exc:  # noqa: BLE001 - any S3 error means "retry from here"
            print(json.dumps({"level": "error", "partition": part, "error": str(exc)}))
            failed_seqs.append(part_first)

    if rejects:
        _put(bucket, f"quarantine/transactions/{shard}-{first_seq}.jsonl.gz", rejects)

    print(json.dumps({"records": len(records), "valid": len(rows), "rejected": len(rejects),
                      "partitions": len(by_part), "failed_partitions": len(failed_seqs)}))
    if failed_seqs:
        # Lambda retries from the lowest failed sequence number onwards
        first_failed = min(failed_seqs, key=int)
        return {"batchItemFailures": [{"itemIdentifier": first_failed}]}
    return {"batchItemFailures": []}
