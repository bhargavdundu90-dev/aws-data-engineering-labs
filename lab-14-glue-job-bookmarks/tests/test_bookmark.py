import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bookmark import Bookmark, S3Object, run_job  # noqa: E402

A = S3Object("loans/dt=2026-11-01/a.csv", 100)
B = S3Object("loans/dt=2026-11-02/b.csv", 200)


def test_first_run_takes_all_then_nothing():
    bm = Bookmark()
    assert run_job(bm, [A, B]) == [A.key, B.key]
    assert run_job(bm, [A, B]) == []


def test_only_new_file_is_picked_up():
    bm = Bookmark()
    run_job(bm, [A])
    assert run_job(bm, [A, B]) == [B.key]


def test_rewritten_file_is_reprocessed():
    bm = Bookmark()
    run_job(bm, [A])
    assert run_job(bm, [S3Object(A.key, 150)]) == [A.key]


def test_failed_run_does_not_advance_bookmark():
    bm = Bookmark()
    assert run_job(bm, [A], fail=True) == []
    assert run_job(bm, [A]) == [A.key]


def test_reset_reprocesses_everything():
    bm = Bookmark()
    run_job(bm, [A, B])
    bm.reset()
    assert run_job(bm, [A, B]) == [A.key, B.key]
