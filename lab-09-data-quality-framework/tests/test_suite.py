import pytest

from dq.suite import SuiteError, load_suite, parse_suite


def test_shipped_suite_loads():
    s = load_suite("suites/card_transactions.yml")
    assert s.dataset == "card_transactions" and s.key == "txn_id"
    names = [c.name for c in s.checks]
    assert len(names) == len(set(names))
    assert {c.type for c in s.checks} >= {"size", "unique", "range", "pattern", "expression",
                                          "row_count_change", "metric_zscore"}
    assert next(c for c in s.checks if c.name == "txn_id_unique").min_compliance == 0.99


@pytest.mark.parametrize("doc, msg", [
    ({"key": "id", "checks": []}, "dataset"),
    ({"dataset": "d", "key": "id", "checks": [{"type": "nope"}]}, "unknown type"),
    ({"dataset": "d", "key": "id", "checks": [{"type": "pattern", "column": "c"}]}, "regex"),
    ({"dataset": "d", "key": "id", "checks": [{"type": "size", "level": "fatal"}]}, "level"),
    ({"dataset": "d", "key": "id", "checks": [{"type": "completeness", "column": "c", "min_compliance": 2}]},
     "min_compliance"),
    ({"dataset": "d", "key": "id", "checks": [{"name": "a", "type": "size"}, {"name": "a", "type": "size"}]},
     "duplicate"),
])
def test_bad_suites_are_rejected(doc, msg):
    with pytest.raises(SuiteError, match=msg):
        parse_suite(doc)
