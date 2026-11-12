# Lab 12 – Athena cost per query, by workgroup

## Scenario
Lab 04 cut the bytes a query scans. The next question from the platform team is *who* is spending:
the monthly Athena line grew, and nobody knows whether it is the fraud dashboard refreshing every
5 minutes or an analyst running `SELECT *` on raw CSV.

Athena already returns `DataScannedInBytes` for every execution. This lab turns that into a cost per
query and a total per workgroup, and lists the queries above a threshold.

## Billing rules the script applies
| Case | Billed |
|------|--------|
| Successful DML query | scanned bytes rounded up to the next MB, minimum 10 MB |
| Cancelled query | what it scanned before cancel (same rounding) |
| Failed query | nothing |
| DDL (`CREATE`, `ALTER`, `DROP`, `MSCK REPAIR`) | nothing |
| Price | `--price-per-tb`, default 5 USD (on-demand; check your region) |

The 10 MB minimum matters for dashboards: 1,000 tiny queries a day still bill as 10 GB.

## How to run
```bash
cd lab-12-athena-query-cost
pip install pytest
pytest -q tests

# with real executions (needs athena:ListQueryExecutions, athena:BatchGetQueryExecution)
aws athena list-query-executions --work-group analytics --max-items 50 \
  --query QueryExecutionIds --output text \
  | xargs aws athena batch-get-query-execution --query-execution-ids > executions.json
python src/query_cost.py executions.json --flag-usd 0.05
```

## What to do with the output
- One workgroup per team or dashboard, so cost lands with whoever runs the queries.
- Set a per-query data-scanned limit on the workgroup (`BytesScannedCutoffPerQuery`) once the
  expensive list shows the normal ceiling.
- Expensive queries are usually `SELECT *`, no partition filter, or CSV/JSON instead of Parquet; lab 04 shows the fixes.
