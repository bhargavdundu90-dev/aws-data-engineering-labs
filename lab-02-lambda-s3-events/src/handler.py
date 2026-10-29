"""Lambda handler: S3 ObjectCreated event -> validate CSV -> curated JSONL + quarantine.

Triggered by an S3 event notification on s3://<bucket>/raw/transactions/*.csv.
Each valid row is written to curated/ as JSON Lines; bad rows go to
quarantine/ with a reason, so nothing is silently dropped.
"""
import csv
import io
import json
import os
import urllib.parse
from datetime import datetime
from decimal import Decimal, InvalidOperation

import boto3

REQUIRED_COLUMNS = ["txn_id", "account_id", "txn_ts", "amount", "currency"]
ALLOWED_CURRENCIES = {"INR", "USD", "EUR", "GBP"}

CURATED_PREFIX = os.environ.get("CURATED_PREFIX", "curated/transactions/")
QUARANTINE_PREFIX = os.environ.get("QUARANTINE_PREFIX", "quarantine/transactions/")

_s3 = None


def s3_client():
    # Created lazily so tests can patch AWS with moto before the first call.
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    return _s3


def validate_row(row):
    """Return (clean_row, None) or (None, reason)."""
    for col in REQUIRED_COLUMNS:
        if not (row.get(col) or "").strip():
            return None, f"missing {col}"
    try:
        amount = Decimal(row["amount"].strip())
    except InvalidOperation:
        return None, "amount not numeric"
    if amount < 0:
        return None, "negative amount"
    currency = row["currency"].strip().upper()
    if currency not in ALLOWED_CURRENCIES:
        return None, f"unknown currency {currency}"
    try:
        ts = datetime.fromisoformat(row["txn_ts"].strip())
    except ValueError:
        return None, "bad txn_ts"
    return {
        "txn_id": row["txn_id"].strip(),
        "account_id": row["account_id"].strip(),
        "txn_ts": ts.isoformat(),
        "txn_date": ts.date().isoformat(),
        "amount": str(amount.quantize(Decimal("0.01"))),
        "currency": currency,
    }, None


def process_object(bucket, key):
    body = s3_client().get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(body))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"{key}: header missing columns {missing}")

    good, bad, seen = [], [], set()
    for line_no, row in enumerate(reader, start=2):
        clean, reason = validate_row(row)
        if clean and clean["txn_id"] in seen:
            clean, reason = None, "duplicate txn_id in file"
        if clean:
            seen.add(clean["txn_id"])
            good.append(clean)
        else:
            bad.append({"line": line_no, "reason": reason, "row": row})

    # Output key is derived from the input key, so a retried event overwrites
    # the same objects instead of creating duplicates (idempotent).
    base = os.path.splitext(os.path.basename(key))[0]
    if good:
        s3_client().put_object(
            Bucket=bucket,
            Key=f"{CURATED_PREFIX}{base}.jsonl",
            Body="\n".join(json.dumps(r) for r in good).encode("utf-8"),
        )
    if bad:
        s3_client().put_object(
            Bucket=bucket,
            Key=f"{QUARANTINE_PREFIX}{base}.rejects.jsonl",
            Body="\n".join(json.dumps(r) for r in bad).encode("utf-8"),
        )
    return {"key": key, "valid": len(good), "rejected": len(bad)}


def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # S3 event keys are URL-encoded (spaces arrive as '+').
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        if not key.lower().endswith(".csv"):
            results.append({"key": key, "skipped": "not a csv"})
            continue
        results.append(process_object(bucket, key))
    print(json.dumps({"processed": results}))
    return {"processed": results}
