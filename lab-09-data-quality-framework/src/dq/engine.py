"""Run a suite against a Spark DataFrame.

All metrics come from one aggregation over the batch: each row check becomes a boolean column, the batch
is cached once, and size, failure counts and means are computed in a single ``agg``. That matters on S3:
ten checks should not mean ten scans of the input.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

from dq.suite import Check, Suite

FAILED_COL = "dq_failed_checks"
WARN_COL = "dq_warn_checks"
SAMPLE_SIZE = 5


@dataclass
class CheckResult:
    name: str
    type: str
    level: str
    column: str | None
    metric: float | None
    threshold: str
    passed: bool
    failed_rows: int = 0
    sample_keys: list | None = None
    note: str = ""


def _ok_column(check: Check, key: str) -> Column:
    """True when the row passes. Nulls pass every check except completeness, which owns them."""
    c, p = check.column, check.params
    if check.type == "completeness":
        return F.col(c).isNotNull()
    if check.type == "unique":
        order = F.col(p["order_by"]) if p.get("order_by") else F.col(c)
        rn = F.row_number().over(Window.partitionBy(c).orderBy(order))
        return F.col(c).isNull() | (rn == 1)   # first occurrence kept, later copies fail
    if check.type == "range":
        cond = F.lit(True)
        if "min" in p:
            cond = cond & (F.col(c) >= F.lit(p["min"]))
        if "max" in p:
            cond = cond & (F.col(c) <= F.lit(p["max"]))
        return F.col(c).isNull() | cond
    if check.type == "allowed_values":
        return F.col(c).isNull() | F.col(c).isin(list(p["values"]))
    if check.type == "pattern":
        return F.col(c).isNull() | F.col(c).cast("string").rlike(p["regex"])
    if check.type == "expression":
        return F.coalesce(F.expr(p["expr"]), F.lit(True))
    raise ValueError(check.type)


def annotate(df: DataFrame, suite: Suite) -> DataFrame:
    """Add one boolean __ok_<name> column per row check plus arrays of failed error / warn checks."""
    out = df
    rows = suite.row_checks()
    for chk in rows:
        out = out.withColumn(f"__ok_{chk.name}", _ok_column(chk, suite.key))

    def failed(level):
        names = [F.when(~F.col(f"__ok_{c.name}"), F.lit(c.name)) for c in rows if c.level == level]
        if not names:
            return F.array().cast("array<string>")
        return F.filter(F.array(*names), lambda x: x.isNotNull())

    return out.withColumn(FAILED_COL, failed("error")).withColumn(WARN_COL, failed("warn"))


def compute_metrics(annotated: DataFrame, suite: Suite) -> dict:
    aggs = [F.count(F.lit(1)).alias("size")]
    for chk in suite.row_checks():
        aggs.append(F.sum(F.when(~F.col(f"__ok_{chk.name}"), 1).otherwise(0)).alias(f"{chk.name}.failed"))
    for chk in suite.checks:
        if chk.type == "metric_zscore":
            aggs.append(F.avg(F.col(chk.column).cast("double")).alias(f"mean.{chk.column}"))
    row = annotated.agg(*aggs).collect()[0].asDict()
    size = int(row["size"])
    metrics = {"size": size}
    for chk in suite.row_checks():
        bad = int(row[f"{chk.name}.failed"] or 0)
        metrics[f"{chk.name}.failed"] = bad
        metrics[f"{chk.name}.compliance"] = 1.0 if size == 0 else round(1 - bad / size, 6)
    for k, v in row.items():
        if k.startswith("mean."):
            metrics[k] = None if v is None else round(float(v), 6)
    return metrics


def _anomaly(chk: Check, metrics: dict, history: list[dict]) -> CheckResult:
    p = chk.params
    if chk.type == "row_count_change":
        prev = [h["metrics"]["size"] for h in history if "size" in h["metrics"]]
        thr = f"|change| <= {p['max_pct']:.0%} vs previous run"
        if not prev:
            return CheckResult(chk.name, chk.type, chk.level, None, None, thr, True, note="no history yet")
        last = prev[-1]
        if last == 0:
            return CheckResult(chk.name, chk.type, chk.level, None, None, thr, metrics["size"] == 0,
                               note="previous run had 0 rows")
        change = (metrics["size"] - last) / last
        return CheckResult(chk.name, chk.type, chk.level, None, round(change, 4), thr,
                           abs(change) <= float(p["max_pct"]), note=f"previous size {last}")

    # metric_zscore: today's mean of a column against the last `window` good runs
    key = f"mean.{chk.column}"
    window = int(p.get("window", 7))
    min_history = int(p.get("min_history", 3))
    vals = [h["metrics"][key] for h in history if h["metrics"].get(key) is not None][-window:]
    thr = f"|z| <= {p['max_z']} over last {window} runs"
    x = metrics.get(key)
    if x is None or len(vals) < min_history:
        return CheckResult(chk.name, chk.type, chk.level, chk.column, None, thr, True,
                           note=f"needs {min_history} earlier runs, has {len(vals)}")
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
    note = f"today {x}, baseline mean {round(mean, 4)}"
    if std == 0:
        # flat baseline: any move is unusual, but z is undefined, so report no number
        return CheckResult(chk.name, chk.type, chk.level, chk.column, None, thr, x == mean,
                           note=note + ", baseline has no variance")
    z = (x - mean) / std
    return CheckResult(chk.name, chk.type, chk.level, chk.column, round(z, 3), thr,
                       abs(z) <= float(p["max_z"]), note=note)


def evaluate(annotated: DataFrame, suite: Suite, metrics: dict, history: list[dict]) -> list[CheckResult]:
    results = []
    for chk in suite.checks:
        if chk.is_row_check:
            comp = metrics[f"{chk.name}.compliance"]
            bad = metrics[f"{chk.name}.failed"]
            res = CheckResult(chk.name, chk.type, chk.level, chk.column, comp,
                              f"compliance >= {chk.min_compliance}", comp >= chk.min_compliance, bad)
            if bad:
                res.sample_keys = [r[0] for r in annotated.filter(~F.col(f"__ok_{chk.name}"))
                                   .select(suite.key).limit(SAMPLE_SIZE).collect()]
        elif chk.type == "size":
            lo, hi = int(chk.params.get("min_rows", 1)), chk.params.get("max_rows")
            n = metrics["size"]
            ok = n >= lo and (hi is None or n <= int(hi))
            res = CheckResult(chk.name, chk.type, chk.level, None, float(n),
                              f"{lo} <= rows" + (f" <= {hi}" if hi is not None else ""), ok)
        else:
            res = _anomaly(chk, metrics, history)
        results.append(res)
    return results


def status_of(results: list[CheckResult]) -> str:
    if any(not r.passed and r.level == "error" for r in results):
        return "FAIL"
    if any(not r.passed for r in results):
        return "WARN"
    return "PASS"


def run_suite(df: DataFrame, suite: Suite, run_date: str, history: list[dict] | None = None):
    """Returns (report, clean_df, quarantine_df).

    clean: rows with no failed error-level row check (warn failures stay in, listed in dq_warn_checks)
    quarantine: the other rows with the names of the checks they failed
    The caller decides what to publish: on FAIL the whole batch is held back.
    """
    annotated = annotate(df, suite).cache()
    metrics = compute_metrics(annotated, suite)
    results = evaluate(annotated, suite, metrics, history or [])
    status = status_of(results)
    helper = [c for c in annotated.columns if c.startswith("__ok_")]
    clean = annotated.filter(F.size(FAILED_COL) == 0).drop(*helper, FAILED_COL)
    quarantine = (annotated.filter(F.size(FAILED_COL) > 0).drop(*helper, WARN_COL)
                  .withColumn("dq_run_date", F.lit(run_date)))
    report = {
        "dataset": suite.dataset,
        "run_date": run_date,
        "status": status,
        "rows": metrics["size"],
        "quarantined_rows": quarantine.count(),
        "metrics": metrics,
        "checks": [asdict(r) for r in results],
    }
    return report, clean, quarantine
