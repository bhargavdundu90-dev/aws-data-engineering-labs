"""Preview who sees what: python -m lfgov.cli --principal risk_analyst --table lending_curated.customers"""
from __future__ import annotations

import argparse
import json

from .access import access_paths, preview
from .policy import load


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Preview Lake Formation access on local sample data")
    ap.add_argument("--policy", default="policy/lake_policy.yml")
    ap.add_argument("--principal", required=True)
    ap.add_argument("--table", required=True, help="database.table")
    ap.add_argument("--csv", help="sample rows for the table (default sample_data/<table>.csv)")
    ap.add_argument("--paths-only", action="store_true")
    args = ap.parse_args(argv)

    policy = load(args.policy)
    database, table = args.table.split(".", 1)
    if args.paths_only:
        print(json.dumps([p.__dict__ for p in access_paths(policy, args.principal, database, table)], indent=2))
        return 0
    result = preview(policy, args.principal, database, table, args.csv or f"sample_data/{table}.csv")
    print(f"{result['principal']} on {result['table']}: {result['access']}")
    if result["columns"]:
        print(" | ".join(result["columns"]))
        for row in result["rows"]:
            print(" | ".join(row[c] for c in result["columns"]))
    print(f"{len(result['rows'])} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
