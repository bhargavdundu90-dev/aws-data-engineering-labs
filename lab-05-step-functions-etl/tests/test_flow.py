"""Run the real ASL file through the local interpreter with stubbed services."""
import json
from pathlib import Path

import pytest

from sfn_local import ExecutionFailed, TaskError, run

ASL = json.loads((Path(__file__).resolve().parents[1] / "state_machine" / "loan_repayments_etl.asl.json").read_text())
INPUT = {"bucket": "bfsi-lake-dev", "key": "landing/loan_repayments/2026-11-02.csv", "business_date": "2026-11-02"}

LAMBDA = "arn:aws:states:::lambda:invoke"
GLUE = "arn:aws:states:::glue:startJobRun.sync"
ATHENA = "arn:aws:states:::athena:startQueryExecution.sync"
SNS = "arn:aws:states:::sns:publish"
COPY = "arn:aws:states:::aws-sdk:s3:copyObject"


class Services:
    def __init__(self, valid=True, rows=120, dq_passed=True, glue_errors=()):
        self.valid, self.rows, self.dq_passed = valid, rows, dq_passed
        self.glue_errors = list(glue_errors)
        self.sns, self.copies, self.glue_calls, self.athena_sql = [], [], [], []

    def lambda_invoke(self, p):
        if p["FunctionName"] == "${validate_function_arn}":
            return {"Payload": {"valid": self.valid, "rows": self.rows, "reason": "" if self.valid else "missing columns: amount"}}
        failures = [] if self.dq_passed else ["3 rows with amount <= 0"]
        return {"Payload": {"passed": self.dq_passed, "failures": failures}}

    def glue(self, p):
        self.glue_calls.append(p)
        if self.glue_errors:
            raise TaskError(self.glue_errors.pop(0))
        return {"Id": "jr_123", "JobRunState": "SUCCEEDED"}

    def athena(self, p):
        self.athena_sql.append(p["QueryString"])
        return {"QueryExecution": {"QueryExecutionId": "q-1"}}

    def publish(self, p):
        self.sns.append(p)
        return {"MessageId": "m-1"}

    def copy(self, p):
        self.copies.append(p)
        return {}

    def resources(self):
        return {LAMBDA: self.lambda_invoke, GLUE: self.glue, ATHENA: self.athena, SNS: self.publish, COPY: self.copy}


def states(history):
    return [name for name, _ in history]


def test_happy_path():
    svc = Services()
    out, history = run(ASL, dict(INPUT), svc.resources())
    assert states(history) == ["ValidateLandedFile", "IsFileValid", "RunGlueTransform", "RunQualityQuery",
                               "EvaluateQuality", "QualityPassed", "NotifySuccess", "Done"]
    assert svc.glue_calls[0]["Arguments"] == {"--source_key": INPUT["key"], "--business_date": "2026-11-02"}
    assert "business_date = '2026-11-02'" in svc.athena_sql[0]
    assert svc.sns[0]["Subject"] == "Loan repayments ETL succeeded"
    assert out["glue"] == {"job_run_id": "jr_123", "state": "SUCCEEDED"}
    assert out["key"] == INPUT["key"]  # ResultPath keeps the original input


def test_invalid_file_is_quarantined_and_glue_never_runs():
    svc = Services(valid=False)
    with pytest.raises(ExecutionFailed) as exc:
        run(ASL, dict(INPUT), svc.resources())
    assert exc.value.error == "FileRejected"
    assert svc.glue_calls == []
    assert svc.copies[0]["Key"] == "quarantine/" + INPUT["key"]
    assert svc.copies[0]["CopySource"] == f"{INPUT['bucket']}/{INPUT['key']}"
    assert "missing columns: amount" in svc.sns[0]["Message"]


def test_glue_concurrency_error_is_retried_then_succeeds():
    svc = Services(glue_errors=["Glue.ConcurrentRunsExceededException"] * 2)
    _, history = run(ASL, dict(INPUT), svc.resources())
    assert [a for n, a in history if n == "RunGlueTransform"] == [1, 2, 3]
    assert states(history)[-1] == "Done"


def test_glue_failure_after_retries_goes_to_notify_failure():
    svc = Services(glue_errors=["Glue.ConcurrentRunsExceededException"] * 10)
    with pytest.raises(ExecutionFailed) as exc:
        run(ASL, dict(INPUT), svc.resources())
    assert exc.value.error == "EtlFailed"
    assert len(svc.glue_calls) == 5  # first try + MaxAttempts 4
    alert = svc.sns[-1]
    assert alert["Subject"] == "Loan repayments ETL failed"
    assert json.loads(alert["Message"])["error"]["Error"] == "Glue.ConcurrentRunsExceededException"


def test_glue_job_failure_is_retried_once():
    svc = Services(glue_errors=["States.TaskFailed", "States.TaskFailed"])
    with pytest.raises(ExecutionFailed):
        run(ASL, dict(INPUT), svc.resources())
    assert len(svc.glue_calls) == 2


def test_quality_gate_failure_alerts_and_fails():
    svc = Services(dq_passed=False)
    with pytest.raises(ExecutionFailed) as exc:
        run(ASL, dict(INPUT), svc.resources())
    assert exc.value.error == "EtlFailed"
    assert "NotifySuccess" not in states(exc.value.history)
    assert json.loads(svc.sns[-1]["Message"])["dq"]["failures"] == ["3 rows with amount <= 0"]
