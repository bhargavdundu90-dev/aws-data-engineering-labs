from datetime import date

from dq.engine import run_suite
from dq.suite import load_suite, parse_suite
from helpers import frame, good_row

SUITE = load_suite("suites/card_transactions.yml")


def check(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def test_clean_batch_passes_with_nothing_quarantined(spark):
    report, clean, quarantine = run_suite(frame(spark, [good_row(i) for i in range(50)]), SUITE, "2026-11-05")
    assert report["status"] == "PASS"
    assert report["rows"] == 50 and report["quarantined_rows"] == 0
    assert clean.count() == 50 and quarantine.count() == 0
    assert check(report, "volume_vs_yesterday")["note"] == "no history yet"


def test_each_broken_row_is_quarantined_with_the_check_it_failed(spark):
    rows = [good_row(i) for i in range(200)]
    rows[1] = good_row(1, amount=-50.0)
    rows[2] = good_row(2, currency="XXX")
    rows[3] = good_row(3, card_bin="41472")
    rows[4] = good_row(4, settlement_date=date(2026, 11, 4))
    rows[5] = good_row(5, account_id=None)
    rows[6] = good_row(6, channel="BRANCH")
    rows.append(dict(rows[10]))                      # replayed record
    report, clean, quarantine = run_suite(frame(spark, rows), SUITE, "2026-11-05")

    got = {r["txn_id"]: r["dq_failed_checks"] for r in quarantine.collect()}
    assert got == {
        "T000001": ["amount_in_range"], "T000002": ["currency_known"], "T000003": ["card_bin_format"],
        "T000004": ["settles_after_txn"], "T000005": ["account_id_present"], "T000006": ["channel_known"],
        "T000010": ["txn_id_unique"],
    }
    assert clean.count() == 201 - 7                  # one copy of T000010 stays
    assert clean.filter("txn_id = 'T000010'").count() == 1
    # card_bin_format and channel_known default to min_compliance 1.0, so one bad row fails the batch
    assert report["status"] == "FAIL"
    failed = {c["name"] for c in report["checks"] if not c["passed"]}
    assert failed == {"card_bin_format", "channel_known"}
    assert check(report, "amount_in_range")["sample_keys"] == ["T000001"]
    assert check(report, "amount_in_range")["metric"] == round(1 - 1 / 201, 6)


def test_warn_checks_do_not_quarantine_or_fail(spark):
    rows = [good_row(i) for i in range(20)]
    rows[0] = good_row(0, mcc="54A1")
    rows[1] = good_row(1, merchant_id=None)
    rows[2] = good_row(2, merchant_id=None)          # 2/20 missing -> below 0.95
    report, clean, quarantine = run_suite(frame(spark, rows), SUITE, "2026-11-05")
    assert report["status"] == "WARN"
    assert quarantine.count() == 0 and clean.count() == 20
    warned = {r["txn_id"]: r["dq_warn_checks"] for r in clean.filter("size(dq_warn_checks) > 0").collect()}
    assert warned == {"T000000": ["mcc_format"], "T000001": ["merchant_id_present"],
                      "T000002": ["merchant_id_present"]}


def test_below_threshold_quarantines_but_batch_still_passes(spark):
    rows = [good_row(i) for i in range(200)]
    rows[7] = good_row(7, amount=0.0)                # 1 of 200 = 99.5% >= 99%
    report, clean, quarantine = run_suite(frame(spark, rows), SUITE, "2026-11-05")
    assert report["status"] == "PASS"
    assert [r.txn_id for r in quarantine.collect()] == ["T000007"]


def test_nulls_only_fail_completeness(spark):
    suite = parse_suite({"dataset": "d", "key": "txn_id", "checks": [
        {"type": "range", "column": "amount", "min": 1},
        {"type": "pattern", "column": "card_bin", "regex": "^[0-9]{6}$"},
        {"type": "completeness", "column": "amount", "level": "warn"},
    ]})
    report, clean, quarantine = run_suite(frame(spark, [good_row(1, amount=None, card_bin=None)]), suite, "d")
    assert quarantine.count() == 0
    assert [c["passed"] for c in report["checks"]] == [True, True, False]


def test_empty_batch_fails_on_size(spark):
    report, _, _ = run_suite(frame(spark, []), SUITE, "2026-11-05")
    assert report["status"] == "FAIL"
    assert not check(report, "has_rows")["passed"]


def test_metrics_come_from_one_aggregation(spark):
    """Size, every failure count and the mean are in the metrics dict without extra jobs per check."""
    report, _, _ = run_suite(frame(spark, [good_row(i, amount=100.0) for i in range(10)]), SUITE, "2026-11-05")
    m = report["metrics"]
    assert m["size"] == 10 and m["mean.amount"] == 100.0
    assert all(m[f"{c.name}.compliance"] == 1.0 for c in SUITE.row_checks())
