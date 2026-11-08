import json

from conftest import SAMPLES
from tfguard.cli import main


def test_good_plan_exit_zero(capsys):
    assert main([str(SAMPLES / "prod_good_plan.json"), "--env", "prod"]) == 0
    assert "OK to apply" in capsys.readouterr().out


def test_risky_plan_blocks_and_writes_report(tmp_path, capsys):
    out = tmp_path / "report.json"
    assert main([str(SAMPLES / "prod_risky_plan.json"), "--env", "prod", "--json", str(out)]) == 1
    assert "BLOCKED" in capsys.readouterr().out
    report = json.loads(out.read_text())
    assert report["blocked"] is True
    assert report["actions"]["replace"] == 1
    assert {f["rule"] for f in report["findings"]} >= {"iam_wildcard", "destructive_change", "bucket_encryption"}


def test_allow_destroy_flag_downgrades_only_named_resource(tmp_path):
    out = tmp_path / "r.json"
    main([str(SAMPLES / "prod_risky_plan.json"), "--env", "prod", "--json", str(out),
          "--allow-destroy", "aws_kms_key.lake"])
    findings = json.loads(out.read_text())["findings"]
    destructive = {f["address"]: f["severity"] for f in findings if f["rule"] == "destructive_change"}
    assert destructive == {"aws_kms_key.lake": "warn", "module.raw_db.aws_glue_catalog_database.this": "error"}
