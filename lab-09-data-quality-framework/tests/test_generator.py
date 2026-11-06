import re
from datetime import date

from make_transactions import make_rows


def test_clean_day_has_no_defects_and_valid_fields():
    rows = make_rows(date(2026, 11, 5), 300)
    assert len({r["txn_id"] for r in rows}) == 300
    for r in rows:
        assert re.fullmatch(r"[0-9]{6}", r["card_bin"]) and r["amount"] > 0
        assert r["settlement_date"] >= r["txn_ts"][:10]
        assert (r["merchant_id"] == "") == (r["channel"] == "ATM")


def test_same_seed_same_file_and_defects_injected():
    a, b = make_rows(date(2026, 11, 5), 200, 0.1), make_rows(date(2026, 11, 5), 200, 0.1)
    assert a == b
    assert sum("_defect" in r for r in a) > 5
