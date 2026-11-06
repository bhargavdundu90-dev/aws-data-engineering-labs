from dq.dqdl import to_dqdl
from dq.suite import load_suite, parse_suite


def test_shipped_suite_translates():
    text, skipped = to_dqdl(load_suite("suites/card_transactions.yml"))
    assert text.startswith("Rules = [") and text.rstrip().endswith("]")
    assert 'Completeness "txn_id" >= 1.0' in text
    assert 'Uniqueness "txn_id" >= 0.99' in text
    assert 'ColumnValues "amount" >= 0.01 with threshold >= 0.99' in text
    assert 'ColumnValues "amount" <= 500000 with threshold >= 0.99' in text
    assert 'ColumnValues "card_bin" matches "^[0-9]{6}$"' in text
    assert 'ColumnValues "currency" in ["INR", "USD", "EUR", "GBP", "AED", "SGD"] with threshold >= 0.99' in text
    assert "RowCount >= avg(last(1)) * 0.5" in text and "RowCount <= avg(last(1)) * 1.5" in text
    assert skipped == ["avg_ticket_size"]


def test_expression_rules():
    suite = parse_suite({"dataset": "d", "key": "id", "checks": [
        {"name": "strict", "type": "expression", "expr": "a >= b"},
        {"name": "loose", "type": "expression", "expr": "a >= b", "min_compliance": 0.9},
    ]})
    text, _ = to_dqdl(suite)
    assert 'CustomSql "select count(*) from primary where not (a >= b)" = 0' in text
    assert 'CustomSql "select avg(case when a >= b then 1.0 else 0.0 end) from primary" >= 0.9' in text


def test_rules_are_comma_separated():
    text, _ = to_dqdl(load_suite("suites/card_transactions.yml"))
    body = text.strip()[len("Rules = ["):-1].strip().splitlines()
    assert all(line.rstrip().endswith(",") for line in body[:-1])
    assert not body[-1].rstrip().endswith(",")
