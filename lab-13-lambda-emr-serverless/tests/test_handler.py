import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import handler  # noqa: E402

ENV = {
    "EMR_APPLICATION_ID": "app-test",
    "EMR_EXECUTION_ROLE_ARN": "arn:aws:iam::000000000000:role/test",
    "SPARK_ENTRYPOINT": "s3://code/job.py",
}


class FakeEmr:
    def __init__(self):
        self.calls = []

    def start_job_run(self, **kw):
        self.calls.append(kw)
        return {"jobRunId": f"run-{len(self.calls)}"}


def event(*keys):
    return {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": k}}} for k in keys]}


def test_builds_request_with_args():
    req = handler.build_job_request("b", "landing/card_txn/dt=2026-11-18/part-1.csv", ENV)
    args = req["jobDriver"]["sparkSubmit"]["entryPointArguments"]
    assert args == ["--input", "s3://b/landing/card_txn/dt=2026-11-18/part-1.csv",
                    "--domain", "card_txn", "--dt", "2026-11-18"]
    assert req["name"] == "card_txn-2026-11-18"


def test_non_input_key_is_skipped():
    assert handler.build_job_request("b", "landing/card_txn/_SUCCESS", ENV) is None


def test_client_token_is_stable():
    k = "landing/loans/dt=2026-11-18/a.json"
    a = handler.build_job_request("b", k, ENV)["clientToken"]
    assert a == handler.build_job_request("b", k, ENV)["clientToken"]
    assert len(a) == 32


def test_url_encoded_key_decoded():
    assert list(handler.parse_records(event("landing/kyc/dt%3D2026-11-18/x.csv"))) == [
        ("b", "landing/kyc/dt=2026-11-18/x.csv")]


def test_handler_starts_and_skips(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    fake = FakeEmr()
    out = handler.handler(event("landing/kyc/dt=2026-11-18/x.csv", "tmp/ignore.txt"), client=fake)
    assert out == {"started": ["run-1"], "skipped": ["tmp/ignore.txt"]}
