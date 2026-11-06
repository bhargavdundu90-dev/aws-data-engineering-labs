from dq.engine import run_suite
from dq.repository import MetricsRepository
from dq.suite import load_suite
from helpers import frame, good_row

SUITE = load_suite("suites/card_transactions.yml")


def seed(repo, sizes_means):
    for i, (size, mean) in enumerate(sizes_means, start=1):
        repo.save("card_transactions", f"2026-11-0{i}", "PASS", {"size": size, "mean.amount": mean})


def check(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def test_repository_replaces_a_rerun_and_hides_failed_runs(tmp_path):
    repo = MetricsRepository(tmp_path / "m.jsonl")
    repo.save("card_transactions", "2026-11-01", "PASS", {"size": 1})
    repo.save("card_transactions", "2026-11-01", "PASS", {"size": 2})
    repo.save("card_transactions", "2026-11-02", "FAIL", {"size": 9999})
    repo.save("other", "2026-11-01", "PASS", {"size": 5})
    assert [h["metrics"]["size"] for h in repo.history("card_transactions", "2026-11-03")] == [2]
    assert len(repo.history("card_transactions", "2026-11-03", include_failed=True)) == 2
    assert repo.history("card_transactions", "2026-11-01") == []


def test_volume_drop_fails_the_batch(spark, tmp_path):
    repo = MetricsRepository(tmp_path / "m.jsonl")
    seed(repo, [(100, 1050.0)])
    report, _, _ = run_suite(frame(spark, [good_row(i) for i in range(40)]), SUITE, "2026-11-02",
                             repo.history("card_transactions", "2026-11-02"))
    c = check(report, "volume_vs_yesterday")
    assert c["metric"] == -0.6 and not c["passed"]
    assert report["status"] == "FAIL"


def test_volume_within_band_passes(spark, tmp_path):
    repo = MetricsRepository(tmp_path / "m.jsonl")
    seed(repo, [(50, 1024.5)])
    report, _, _ = run_suite(frame(spark, [good_row(i) for i in range(60)]), SUITE, "2026-11-02",
                             repo.history("card_transactions", "2026-11-02"))
    assert check(report, "volume_vs_yesterday")["passed"]


def test_avg_ticket_jump_is_a_warning(spark, tmp_path):
    repo = MetricsRepository(tmp_path / "m.jsonl")
    seed(repo, [(20, 1000.0), (20, 1010.0), (20, 990.0), (20, 1005.0)])
    rows = [good_row(i, amount=1000.0 * 100) for i in range(20)]   # paise sent as rupees
    report, _, _ = run_suite(frame(spark, rows), SUITE, "2026-11-05", repo.history("card_transactions", "2026-11-05"))
    c = check(report, "avg_ticket_size")
    assert not c["passed"] and c["metric"] > 3
    assert report["status"] == "WARN"                # level: warn in the suite


def test_zscore_waits_for_enough_history(spark, tmp_path):
    repo = MetricsRepository(tmp_path / "m.jsonl")
    seed(repo, [(20, 1000.0), (20, 1010.0)])
    report, _, _ = run_suite(frame(spark, [good_row(i) for i in range(20)]), SUITE, "2026-11-03",
                             repo.history("card_transactions", "2026-11-03"))
    c = check(report, "avg_ticket_size")
    assert c["passed"] and c["metric"] is None and "needs 3" in c["note"]
