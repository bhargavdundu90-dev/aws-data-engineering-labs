import json
from pathlib import Path

from run_dq import main
from test_llm_dq import FakeBedrock, flag

SAMPLE = str(Path(__file__).resolve().parents[1] / "sample_data" / "kyc_applications.csv")


def test_dry_run_prints_masked_prompt(capsys):
    assert main(["--input", SAMPLE, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would go to the model" in out and "Records:" in out
    assert "ABCPK1234L" not in out


def test_end_to_end_writes_results(tmp_path, capsys):
    out = tmp_path / "res.jsonl"
    rc = main(["--input", SAMPLE, "--out", str(out), "--cache", str(tmp_path / "c.json")],
              client=FakeBedrock(flag({})))
    assert rc == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 10 and {"application_id", "status", "rule_issues", "llm_issues"} <= set(rows[0])
    assert "bedrock calls: 2" in capsys.readouterr().out
