import boto3
import pytest
from moto import mock_aws

from handlers import check_quality, evaluate_quality, validate_landed_file

BUCKET = "bfsi-lake-dev"
HEADER = "loan_id,customer_id,business_date,amount,channel\n"


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-south-1")
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        yield client


def put(s3, key, body):
    s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode())
    return {"bucket": BUCKET, "key": key, "business_date": "2026-11-02"}


def test_valid_file(s3):
    body = HEADER + "L1,C1,2026-11-02,2500.00,UPI\nL2,C9,2026-11-02,1800.50,NACH\n"
    assert validate_landed_file(put(s3, "landing/a.csv", body)) == {"valid": True, "rows": 2, "reason": ""}


def test_empty_file(s3):
    assert validate_landed_file(put(s3, "landing/e.csv", ""))["reason"] == "empty file"


def test_missing_column(s3):
    out = validate_landed_file(put(s3, "landing/m.csv", "loan_id,customer_id,business_date,channel\nL1,C1,2026-11-02,UPI\n"))
    assert not out["valid"] and out["reason"] == "missing columns: amount"


def test_wrong_business_date(s3):
    body = HEADER + "L1,C1,2026-11-02,10,UPI\nL2,C2,2026-11-01,10,UPI\n"
    out = validate_landed_file(put(s3, "landing/d.csv", body))
    assert not out["valid"] and out["reason"].startswith("line 3:")


def test_header_only(s3):
    assert validate_landed_file(put(s3, "landing/h.csv", HEADER))["reason"] == "header only, no rows"


def test_missing_object_raises_so_step_functions_can_retry(s3):
    with pytest.raises(Exception):
        validate_landed_file({"bucket": BUCKET, "key": "landing/nope.csv", "business_date": "2026-11-02"})


def test_check_quality_rules():
    assert check_quality(10, 0, 0, 10) == {"passed": True, "failures": []}
    out = check_quality(9, 1, 2, 10)
    assert not out["passed"] and len(out["failures"]) == 3


class FakeAthena:
    def get_query_results(self, QueryExecutionId):
        cols = ["total", "null_loan_id", "bad_amount"]
        return {"ResultSet": {"Rows": [
            {"Data": [{"VarCharValue": c} for c in cols]},
            {"Data": [{"VarCharValue": "120"}, {"VarCharValue": "0"}, {"VarCharValue": "0"}]},
        ]}}


def test_evaluate_quality_reads_athena_result():
    assert evaluate_quality({"query_id": "q-1", "expected_rows": 120}, athena=FakeAthena())["passed"]
    assert not evaluate_quality({"query_id": "q-1", "expected_rows": 121}, athena=FakeAthena())["passed"]
