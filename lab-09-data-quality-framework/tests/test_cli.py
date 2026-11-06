import json
from datetime import date

from dq.cli import main, run
from make_transactions import make_rows, write_csv


def test_generated_day_with_defects_runs_end_to_end(spark, tmp_path):
    data = write_csv(make_rows(date(2026, 11, 5), 400, bad_rate=0.03), tmp_path / "in.csv")
    report = run(spark, str(data), "suites/card_transactions.yml", "2026-11-05", tmp_path / "m.jsonl",
                 tmp_path / "out")
    assert report["rows"] == 400
    assert report["quarantined_rows"] > 0
    assert (tmp_path / "out" / "report" / "2026-11-05.json").exists()
    q = spark.read.csv(str(tmp_path / "out" / "quarantine" / "run_date=2026-11-05"), header=True)
    assert q.count() == report["quarantined_rows"]
    if report["status"] != "FAIL":
        clean = spark.read.parquet(str(tmp_path / "out" / "clean" / "run_date=2026-11-05"))
        assert clean.count() + q.count() == 400
    saved = json.loads((tmp_path / "m.jsonl").read_text().splitlines()[0])
    assert saved["run_date"] == "2026-11-05" and saved["metrics"]["size"] == 400


def test_failed_batch_is_not_published(spark, tmp_path):
    rows = make_rows(date(2026, 11, 5), 100)
    for r in rows[:5]:
        r["card_bin"] = "1234"                       # card_bin_format needs 100%
    data = write_csv(rows, tmp_path / "in.csv")
    report = run(spark, str(data), "suites/card_transactions.yml", "2026-11-05", tmp_path / "m.jsonl",
                 tmp_path / "out")
    assert report["status"] == "FAIL"
    assert not (tmp_path / "out" / "clean").exists()


def test_print_dqdl(capsys):
    assert main(["--data", "x", "--suite", "suites/card_transactions.yml", "--run-date", "d", "--print-dqdl"]) == 0
    assert capsys.readouterr().out.startswith("Rules = [")
