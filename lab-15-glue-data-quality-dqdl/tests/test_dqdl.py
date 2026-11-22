import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dqdl import evaluate, evaluate_ruleset, parse_rule  # noqa: E402

ROWS = [
    {"txn_id": "t1", "amount": 100.0, "channel": "UPI"},
    {"txn_id": "t2", "amount": 2500.5, "channel": "ATM"},
]


def test_row_count():
    assert evaluate("RowCount >= 2", ROWS)
    assert not evaluate("RowCount > 2", ROWS)


def test_complete_and_unique():
    assert evaluate('IsComplete "txn_id"', ROWS)
    assert evaluate('IsUnique "txn_id"', ROWS)
    dup = ROWS + [{"txn_id": "t1", "amount": 1, "channel": "UPI"}]
    assert not evaluate('IsUnique "txn_id"', dup)
    assert not evaluate('IsComplete "txn_id"', ROWS + [{"txn_id": None}])


def test_between_and_in():
    assert evaluate('ColumnValues "amount" between 0 and 10000', ROWS)
    assert not evaluate('ColumnValues "amount" between 0 and 1000', ROWS)
    assert evaluate('ColumnValues "channel" in ["UPI","ATM","POS"]', ROWS)
    assert not evaluate('ColumnValues "channel" in ["UPI"]', ROWS)


def test_ruleset_and_unsupported():
    res = evaluate_ruleset(["RowCount = 2", 'IsUnique "channel"'], ROWS)
    assert res == {"RowCount = 2": True, 'IsUnique "channel"': True}
    with pytest.raises(ValueError):
        parse_rule("Mean 1")
