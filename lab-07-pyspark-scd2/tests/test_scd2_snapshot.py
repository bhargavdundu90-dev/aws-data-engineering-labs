from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pyspark.sql import functions as F

from make_snapshots import generate, write
from scd2_snapshot import (SNAPSHOT_SCHEMA, TYPE2, SnapshotError, apply_snapshot, as_of, empty_dim,
                           load_all, read_snapshot)

D1, D2, D3 = date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)
OPEN = date(9999, 12, 31)
SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "snapshots"


def acc(acc_id, rate="9.00", branch="HYD001", status="ACTIVE", name=None, mobile="9000000000", emi="10000.00"):
    return (acc_id, name or f"Customer {acc_id}", mobile, "HL", Decimal(rate), Decimal(emi), branch, status)


def snap(spark, *rows):
    return spark.createDataFrame(list(rows), SNAPSHOT_SCHEMA)


def versions(dim, acc_id):
    return [r.asDict() for r in dim.where(F.col("account_id") == acc_id).orderBy("effective_from").collect()]


def load(spark, *days):
    dim = empty_dim(spark)
    for d, rows in days:
        dim = apply_snapshot(dim, snap(spark, *rows), d)
    return dim


def test_first_load_inserts_everything_as_current(spark):
    dim = load(spark, (D1, [acc("A"), acc("B")]))
    rows = dim.collect()
    assert len(rows) == 2
    assert all(r.is_current and r.effective_from == D1 and r.effective_to == OPEN and r.change_type == "insert"
               for r in rows)


def test_type2_change_closes_old_version_and_opens_new(spark):
    dim = load(spark, (D1, [acc("A", rate="9.00")]), (D2, [acc("A", rate="9.25", emi="10150.00")]))
    v = versions(dim, "A")
    assert [(x["interest_rate"], x["effective_from"], x["effective_to"], x["is_current"]) for x in v] == [
        (Decimal("9.00"), D1, D2, False), (Decimal("9.25"), D2, OPEN, True)]
    assert v[1]["change_type"] == "update"


def test_unchanged_account_gets_no_new_version(spark):
    dim = load(spark, (D1, [acc("A"), acc("B")]), (D2, [acc("A"), acc("B", branch="BLR004")]))
    assert len(versions(dim, "A")) == 1 and len(versions(dim, "B")) == 2


def test_type1_correction_overwrites_all_versions_without_new_version(spark):
    dim = load(spark, (D1, [acc("A", rate="9.00")]),
               (D2, [acc("A", rate="9.50")]),
               (D3, [acc("A", rate="9.50", name="CUSTOMER A", mobile="9111111111")]))
    v = versions(dim, "A")
    assert len(v) == 2
    assert {x["customer_name"] for x in v} == {"CUSTOMER A"}
    assert {x["mobile"] for x in v} == {"9111111111"}


def test_missing_account_is_closed_and_reopen_is_labelled(spark):
    dim = load(spark, (D1, [acc("A"), acc("B")]), (D2, [acc("A")]), (D3, [acc("A"), acc("B")]))
    v = versions(dim, "B")
    assert [(x["effective_from"], x["effective_to"], x["is_current"], x["change_type"]) for x in v] == [
        (D1, D2, False, "insert"), (D3, OPEN, True, "reopen")]
    assert as_of(dim, D2).where("account_id = 'B'").count() == 0  # closed on that day


def test_closed_account_keeps_last_type1_values(spark):
    dim = load(spark, (D1, [acc("A"), acc("B", mobile="9222222222")]), (D2, [acc("A")]))
    assert versions(dim, "B")[0]["mobile"] == "9222222222"


def test_null_to_value_is_a_change(spark):
    a = acc("A")
    a_null_branch = a[:6] + (None,) + a[7:]
    dim = load(spark, (D1, [a_null_branch]), (D2, [a]))
    assert [x["branch_code"] for x in versions(dim, "A")] == [None, "HYD001"]


def test_rerunning_the_same_snapshot_is_a_no_op(spark):
    rows = [acc("A"), acc("B", status="SMA1")]
    once = load(spark, (D1, [acc("A"), acc("B")]), (D2, rows))
    twice = apply_snapshot(once, snap(spark, *rows), D2)
    key = lambda df: sorted(tuple(r) for r in df.collect())  # noqa: E731
    assert key(twice) == key(once)


@pytest.mark.parametrize("rows, message", [
    ([acc("A"), acc("A", rate="9.10")], "duplicate"),
    ([], "empty"),
])
def test_bad_snapshot_is_refused_before_touching_the_dim(spark, rows, message):
    dim = load(spark, (D1, [acc("A")]))
    bad = snap(spark, *rows) if rows else spark.createDataFrame([], SNAPSHOT_SCHEMA)
    with pytest.raises(SnapshotError, match=message):
        apply_snapshot(dim, bad, D2)


def test_older_snapshot_is_refused(spark):
    dim = load(spark, (D1, [acc("A")]), (D2, [acc("A", rate="9.10")]))
    with pytest.raises(SnapshotError, match="older"):
        apply_snapshot(dim, snap(spark, acc("A")), D1)


def test_generated_history_rebuilds_every_snapshot_and_keeps_invariants(spark, tmp_path):
    snapshots = generate(days=6, accounts=25, seed=11, change_rate=0.35)
    write(snapshots, tmp_path)
    dim = load_all(spark, tmp_path)

    # point-in-time view on each snapshot date == that day's extract (Type 2 columns)
    for d, rows in snapshots:
        got = sorted((r.account_id, *[str(r[c]) for c in TYPE2]) for r in as_of(dim, d).collect())
        want = sorted((r["account_id"], *[f"{r[c]:.2f}" if isinstance(r[c], float) else str(r[c]) for c in TYPE2])
                      for r in rows)
        assert got == want, d

    assert dim.where("is_current").groupBy("account_id").count().where("count > 1").count() == 0
    assert dim.where("effective_from >= effective_to").count() == 0
    assert dim.groupBy("account_sk").count().where("count > 1").count() == 0


def test_sample_snapshots_load(spark):
    dim = load_all(spark, SAMPLE)
    types = {r.change_type for r in dim.collect()}
    assert {"insert", "update", "reopen"} <= types
    last = read_snapshot(spark, str(SAMPLE / "loan_accounts_2026-11-04.csv"))
    assert dim.where("is_current").count() == last.count()
