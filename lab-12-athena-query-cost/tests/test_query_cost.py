import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from query_cost import (MB, MIN_BILLED_BYTES, TB, billed_bytes, by_workgroup, cost_of,  # noqa: E402
                        expensive, load_executions, main)


def execution(qid, scanned, state="SUCCEEDED", stype="DML", wg="analytics", sql="SELECT 1"):
    return {
        "QueryExecutionId": qid,
        "Query": sql,
        "StatementType": stype,
        "WorkGroup": wg,
        "Status": {"State": state},
        "Statistics": {"DataScannedInBytes": scanned},
    }


def test_small_query_billed_at_10_mb_minimum():
    assert billed_bytes(1, "SUCCEEDED", "DML") == MIN_BILLED_BYTES
    assert billed_bytes(0, "SUCCEEDED", "DML") == MIN_BILLED_BYTES


def test_rounds_up_to_next_mb():
    assert billed_bytes(50 * MB + 1, "SUCCEEDED", "DML") == 51 * MB


def test_failed_and_ddl_are_free_cancelled_is_billed():
    assert billed_bytes(5 * TB, "FAILED", "DML") == 0
    assert billed_bytes(5 * TB, "SUCCEEDED", "DDL") == 0
    assert billed_bytes(200 * MB, "CANCELLED", "DML") == 200 * MB
    assert billed_bytes(0, "CANCELLED", "DML") == 0
    assert billed_bytes(200 * MB, "RUNNING", "DML") == 0


def test_one_tb_costs_the_price_per_tb():
    assert cost_of(execution("q1", TB), price_per_tb=5.0).cost_usd == pytest.approx(5.0)


def test_workgroup_totals_and_expensive_list():
    costs = [
        cost_of(execution("a", 2 * TB, sql="SELECT * FROM card_txn")),
        cost_of(execution("b", 1 * MB)),
        cost_of(execution("c", 100 * 1024 ** 3, wg="fraud-dashboard")),
        cost_of(execution("d", TB, state="FAILED")),
    ]
    totals = by_workgroup(costs)
    assert totals["analytics"]["queries"] == 3
    assert totals["analytics"]["cost_usd"] == pytest.approx(10.0 + 10 * MB / TB * 5, rel=1e-4)
    assert totals["fraud-dashboard"]["cost_usd"] == pytest.approx(100 / 1024 * 5, rel=1e-4)
    assert [c.query_id for c in expensive(costs, 0.05)] == ["a", "c"]


def test_load_accepts_batch_single_and_list():
    e = execution("x", MB)
    assert len(load_executions({"QueryExecutions": [e, e]})) == 2
    assert load_executions({"QueryExecution": e}) == [e]
    assert load_executions([{"QueryExecution": e}, e]) == [e, e]
    with pytest.raises(ValueError):
        load_executions({"Foo": 1})


def test_cli_prints_workgroups(tmp_path, capsys):
    path = tmp_path / "executions.json"
    path.write_text(json.dumps({"QueryExecutions": [execution("a", 2 * TB, sql="SELECT * FROM card_txn")]}))
    assert main([str(path), "--flag-usd", "1"]) == 0
    out = capsys.readouterr().out
    assert "analytics" in out and "SELECT * FROM card_txn" in out
