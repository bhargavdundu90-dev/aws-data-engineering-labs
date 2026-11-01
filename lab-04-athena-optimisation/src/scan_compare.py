"""Measure how much data a typical query scans in three layouts.

Query: total card spend per merchant_category for one region on one day,
which touches 3 columns out of 10.

  A. one big CSV            -> Athena reads every byte of every file
  B. Parquet, unpartitioned  -> reads only the 3 column chunks, but of every day
  C. Parquet, region/dt     -> reads only the 3 column chunks of one partition

Bytes for Parquet come from the real column-chunk sizes in the file footers,
which is what Athena reads (plus the small footer itself).

    python src/scan_compare.py --days 30 --rows-per-day 20000
"""
from __future__ import annotations

import argparse
import csv
import random
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from projection import query_cost_usd

REGIONS = ["south", "west", "north", "east"]
CATEGORIES = ["grocery", "fuel", "travel", "electronics", "dining", "utilities"]
COLUMNS = ["txn_id", "card_id", "customer_id", "txn_ts", "amount", "currency",
           "merchant_id", "merchant_category", "channel", "region"]
QUERY_COLS = ["amount", "merchant_category", "region"]


def make_rows(day: date, n: int, rng: random.Random):
    for i in range(n):
        yield {
            "txn_id": f"T{day:%Y%m%d}{i:07d}",
            "card_id": f"C{rng.randrange(10**6):06d}",
            "customer_id": f"CU{rng.randrange(10**5):05d}",
            "txn_ts": f"{day.isoformat()}T{rng.randrange(24):02d}:{rng.randrange(60):02d}:00",
            "amount": round(rng.lognormvariate(6, 1.2), 2),
            "currency": "INR",
            "merchant_id": f"M{rng.randrange(5000):04d}",
            "merchant_category": rng.choice(CATEGORIES),
            "channel": rng.choice(["POS", "ECOM", "UPI"]),
            "region": rng.choice(REGIONS),
        }


def column_bytes(path: Path, cols: list[str]) -> int:
    md = pq.ParquetFile(path).metadata
    total = md.serialized_size  # footer is always read
    for rg in range(md.num_row_groups):
        group = md.row_group(rg)
        for c in range(group.num_columns):
            chunk = group.column(c)
            if chunk.path_in_schema in cols:
                total += chunk.total_compressed_size
    return total


def build(root: Path, days: int, rows_per_day: int, seed: int = 42, start=date(2026, 10, 1)):
    rng = random.Random(seed)
    csv_path = root / "csv" / "transactions.csv"
    csv_path.parent.mkdir(parents=True)
    flat_tables, part_files = [], {}
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for k in range(days):
            day = start + timedelta(days=k)
            rows = list(make_rows(day, rows_per_day, rng))
            w.writerows(rows)
            table = pa.Table.from_pylist(rows)
            flat_tables.append(table)
            for region in REGIONS:
                part = table.filter(pc.equal(table["region"], region))
                p = root / "parquet_part" / f"region={region}" / f"dt={day.isoformat()}" / "part-0.parquet"
                p.parent.mkdir(parents=True)
                pq.write_table(part, p, compression="snappy")
                part_files[(region, day)] = p
    flat = root / "parquet_flat" / "part-0.parquet"
    flat.parent.mkdir(parents=True)
    pq.write_table(pa.concat_tables(flat_tables), flat, compression="snappy")
    return csv_path, flat, part_files


def compare(root: Path, days: int, rows_per_day: int, region="south", day: date | None = None):
    csv_path, flat, parts = build(root, days, rows_per_day)
    day = day or min(d for _, d in parts) + timedelta(days=days // 2)
    scans = {
        "A csv, no partitions": csv_path.stat().st_size,
        "B parquet, no partitions": column_bytes(flat, QUERY_COLS),
        "C parquet, region/dt": column_bytes(parts[(region, day)], QUERY_COLS),
    }
    return {k: (v, query_cost_usd(v)) for k, v in scans.items()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--rows-per-day", type=int, default=20000)
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        res = compare(Path(tmp), a.days, a.rows_per_day)
    base = res["A csv, no partitions"][0]
    print(f"{'layout':<26}{'scanned':>12}{'vs csv':>9}{'cost/query':>13}{'cost x10k/day':>15}")
    for name, (b, usd) in res.items():
        print(f"{name:<26}{b/1024**2:>10.2f}MB{b/base:>8.1%}{"$" + format(usd, ".6f"):>13}{"$" + format(usd * 10_000, ".2f"):>15}")
    print("note: anything under 10 MB is still billed as 10 MB, so tiny partitions do not get cheaper than that")


if __name__ == "__main__":
    main()
