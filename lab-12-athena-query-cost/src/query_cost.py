"""Cost per Athena query, from the statistics Athena already returns.

Athena (engine v2/v3, on-demand) bills per query on DataScannedInBytes:
  * rounded up to the next whole MB, with a 10 MB minimum per query
  * failed queries are not billed; cancelled queries are billed for what they scanned
  * DDL statements (CREATE/ALTER/DROP, MSCK) are free

Input is the JSON of `aws athena batch-get-query-execution` (or a list of
`get-query-execution` results). Output is a per-query cost line plus totals
per workgroup, and the queries over a cost threshold, so a team can see which
dashboard or analyst is driving the bill.

    aws athena list-query-executions --work-group analytics --max-items 50 \
      --query QueryExecutionIds --output text \
      | xargs aws athena batch-get-query-execution --query-execution-ids > executions.json
    python src/query_cost.py executions.json --price-per-tb 5 --flag-usd 0.05
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

MB = 1024 ** 2
TB = 1024 ** 4
MIN_BILLED_BYTES = 10 * MB
DEFAULT_PRICE_PER_TB = 5.0  # USD, us-east-1 / ap-south-1 on-demand; check your region


@dataclass(frozen=True)
class QueryCost:
    query_id: str
    workgroup: str
    state: str
    statement_type: str
    scanned_bytes: int
    billed_bytes: int
    cost_usd: float
    query: str


def billed_bytes(scanned: int, state: str, statement_type: str) -> int:
    """Bytes Athena charges for one query execution."""
    if statement_type == "DDL" or state == "FAILED":
        return 0
    if state not in ("SUCCEEDED", "CANCELLED"):
        return 0  # QUEUED / RUNNING: nothing final yet
    if scanned <= 0 and state == "CANCELLED":
        return 0
    rounded = math.ceil(max(scanned, 0) / MB) * MB
    return max(rounded, MIN_BILLED_BYTES)


def cost_of(execution: dict, price_per_tb: float = DEFAULT_PRICE_PER_TB) -> QueryCost:
    stats = execution.get("Statistics", {})
    state = execution.get("Status", {}).get("State", "UNKNOWN")
    stype = execution.get("StatementType", "DML")
    scanned = int(stats.get("DataScannedInBytes", 0))
    billed = billed_bytes(scanned, state, stype)
    return QueryCost(
        query_id=execution.get("QueryExecutionId", "?"),
        workgroup=execution.get("WorkGroup", "primary"),
        state=state,
        statement_type=stype,
        scanned_bytes=scanned,
        billed_bytes=billed,
        cost_usd=round(billed / TB * price_per_tb, 6),
        query=" ".join(execution.get("Query", "").split()),
    )


def load_executions(payload: dict | list) -> list[dict]:
    if isinstance(payload, dict):
        if "QueryExecutions" in payload:
            return list(payload["QueryExecutions"])
        if "QueryExecution" in payload:
            return [payload["QueryExecution"]]
        raise ValueError("expected QueryExecutions or QueryExecution in the JSON")
    out = []
    for item in payload:
        out.append(item.get("QueryExecution", item))
    return out


def by_workgroup(costs: list[QueryCost]) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: {"queries": 0, "scanned_gb": 0.0, "cost_usd": 0.0})
    for c in costs:
        t = totals[c.workgroup]
        t["queries"] += 1
        t["scanned_gb"] += c.scanned_bytes / 1024 ** 3
        t["cost_usd"] += c.cost_usd
    return {wg: {k: round(v, 6) for k, v in t.items()} for wg, t in sorted(totals.items())}


def expensive(costs: list[QueryCost], flag_usd: float) -> list[QueryCost]:
    return sorted((c for c in costs if c.cost_usd >= flag_usd), key=lambda c: c.cost_usd, reverse=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("executions", type=Path, help="batch-get-query-execution JSON")
    ap.add_argument("--price-per-tb", type=float, default=DEFAULT_PRICE_PER_TB)
    ap.add_argument("--flag-usd", type=float, default=0.05, help="list queries costing at least this")
    args = ap.parse_args(argv)

    costs = [cost_of(e, args.price_per_tb) for e in load_executions(json.loads(args.executions.read_text()))]
    print(f"{'workgroup':<14}{'queries':>8}{'scanned GB':>12}{'cost USD':>10}")
    for wg, t in by_workgroup(costs).items():
        print(f"{wg:<14}{int(t['queries']):>8}{t['scanned_gb']:>12.2f}{t['cost_usd']:>10.4f}")
    flagged = expensive(costs, args.flag_usd)
    if flagged:
        print(f"\nqueries costing >= ${args.flag_usd}:")
        for c in flagged:
            print(f"  ${c.cost_usd:.4f}  {c.workgroup:<12} {c.query[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
