"""Lambda handlers used by the loan repayments state machine.

validate_landed_file  - checks the landed CSV before any Glue money is spent
evaluate_quality      - reads the Athena data-quality query result and decides pass/fail
"""
from __future__ import annotations

import csv
import io
import os

import boto3

REQUIRED_COLUMNS = ["loan_id", "customer_id", "business_date", "amount", "channel"]
MAX_BYTES = 512 * 1024 * 1024  # anything bigger goes through the bulk path, not this flow


def _s3():
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))


def validate_landed_file(event, context=None):
    """Input: {"bucket", "key", "business_date"}. Output: {"valid", "rows", "reason"}.

    Returns valid=False (instead of raising) for bad data, so the state machine can route
    it to quarantine. Raises only for infrastructure problems, which Step Functions retries.
    """
    bucket, key, business_date = event["bucket"], event["key"], event["business_date"]
    s3 = _s3()
    head = s3.head_object(Bucket=bucket, Key=key)
    if head["ContentLength"] == 0:
        return {"valid": False, "rows": 0, "reason": "empty file"}
    if head["ContentLength"] > MAX_BYTES:
        return {"valid": False, "rows": 0, "reason": f"file too large ({head['ContentLength']} bytes)"}

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(body))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        return {"valid": False, "rows": 0, "reason": f"missing columns: {', '.join(missing)}"}

    rows = 0
    for i, row in enumerate(reader, start=2):
        rows += 1
        if row["business_date"] != business_date:
            return {"valid": False, "rows": rows,
                    "reason": f"line {i}: business_date {row['business_date']} != {business_date}"}
    if rows == 0:
        return {"valid": False, "rows": 0, "reason": "header only, no rows"}
    return {"valid": True, "rows": rows, "reason": ""}


def _athena():
    return boto3.client("athena", region_name=os.environ.get("AWS_REGION", "ap-south-1"))


def check_quality(total: int, null_loan_id: int, bad_amount: int, expected_rows: int) -> dict:
    """Pure rule check, kept separate from the Athena call so it is easy to test."""
    failures = []
    if total != expected_rows:
        failures.append(f"row count {total} != landed {expected_rows}")
    if null_loan_id:
        failures.append(f"{null_loan_id} rows with null loan_id")
    if bad_amount:
        failures.append(f"{bad_amount} rows with amount <= 0")
    return {"passed": not failures, "failures": failures}


def evaluate_quality(event, context=None, athena=None):
    """Input: {"query_id", "expected_rows"}. Output: {"passed", "failures"}."""
    athena = athena or _athena()
    result = athena.get_query_results(QueryExecutionId=event["query_id"])
    header, values = result["ResultSet"]["Rows"][0], result["ResultSet"]["Rows"][1]
    names = [c["VarCharValue"] for c in header["Data"]]
    row = {n: int(v.get("VarCharValue", 0)) for n, v in zip(names, values["Data"])}
    return check_quality(row["total"], row["null_loan_id"], row["bad_amount"], int(event["expected_rows"]))
