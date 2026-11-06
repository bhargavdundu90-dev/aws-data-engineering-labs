"""Validate one day's card-transaction file before it is published to the curated zone.

    python -m dq.cli --data sample_data/card_txn_2026-11-05.csv --suite suites/card_transactions.yml \\
        --run-date 2026-11-05 --repo output/metrics.jsonl --out output

Writes  out/report/<run_date>.json, out/quarantine/run_date=<d>/, and (unless the batch FAILED)
out/clean/run_date=<d>/. Exit code 1 on FAIL so the orchestrator (Step Functions / Airflow) stops there.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from dq.dqdl import to_dqdl
from dq.engine import FAILED_COL, run_suite
from dq.repository import MetricsRepository
from dq.suite import load_suite
from schema import TXN_SCHEMA


def run(spark, data, suite_path, run_date, repo_path, out) -> dict:
    suite = load_suite(suite_path)
    repo = MetricsRepository(repo_path)
    df = spark.read.csv(data, header=True, schema=TXN_SCHEMA)
    report, clean, quarantine = run_suite(df, suite, run_date, repo.history(suite.dataset, run_date))

    out = Path(out)
    (quarantine.withColumn(FAILED_COL, F.concat_ws(",", FAILED_COL))   # csv has no arrays
     .coalesce(1).write.mode("overwrite").option("header", True)
     .csv(str(out / "quarantine" / f"run_date={run_date}")))
    if report["status"] != "FAIL":
        clean.drop("dq_warn_checks").coalesce(1).write.mode("overwrite").parquet(
            str(out / "clean" / f"run_date={run_date}"))
    repo.save(suite.dataset, run_date, report["status"], report["metrics"])
    (out / "report").mkdir(parents=True, exist_ok=True)
    (out / "report" / f"{run_date}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run a data-quality suite on a batch")
    p.add_argument("--data", required=True)
    p.add_argument("--suite", required=True)
    p.add_argument("--run-date", required=True)
    p.add_argument("--repo", default="output/metrics.jsonl")
    p.add_argument("--out", default="output")
    p.add_argument("--print-dqdl", action="store_true", help="print the Glue Data Quality ruleset and exit")
    a = p.parse_args(argv)
    if a.print_dqdl:
        text, skipped = to_dqdl(load_suite(a.suite))
        print(text)
        if skipped:
            print("not translated:", ", ".join(skipped), file=sys.stderr)
        return 0
    spark = (SparkSession.builder.appName("dq-batch").config("spark.sql.session.timeZone", "UTC")
             .getOrCreate())
    report = run(spark, a.data, a.suite, a.run_date, a.repo, a.out)
    spark.stop()
    failed = [c["name"] for c in report["checks"] if not c["passed"]]
    print(f"{report['dataset']} {report['run_date']}: {report['status']} "
          f"rows={report['rows']} quarantined={report['quarantined_rows']} failed={failed}")
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
