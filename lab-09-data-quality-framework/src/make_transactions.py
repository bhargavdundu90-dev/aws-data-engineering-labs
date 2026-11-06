"""Daily card-transaction file in the processor's layout, with a controllable share of broken rows.

    python src/make_transactions.py --date 2026-11-05 --rows 400 --bad-rate 0.02 \\
        --out sample_data/card_txn_2026-11-05.csv

Broken rows, picked at random: replayed txn_id, missing account_id, zero or negative amount, unknown
currency, 5-digit card BIN, settlement date before the transaction, bad MCC.
--scale multiplies amounts (to test the avg-ticket anomaly check).
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

COLUMNS = ["txn_id", "account_id", "card_bin", "merchant_id", "mcc", "channel", "amount", "currency",
           "txn_ts", "settlement_date"]
BINS = ["414720", "524193", "607153", "652150", "438857"]       # Visa, Mastercard, RuPay ranges
MCC = {"5411": (150, 4000), "5812": (200, 3000), "5541": (500, 6000), "5732": (2000, 90000),
       "4511": (3000, 45000), "5999": (100, 2500)}
CURRENCIES = ["INR"] * 18 + ["USD", "AED"]
DEFECTS = ["dup_txn_id", "null_account", "bad_amount", "bad_currency", "bad_bin", "settle_before_txn",
           "bad_mcc"]


def make_rows(day: date, n: int, bad_rate: float = 0.0, seed: int = 7, scale: float = 1.0) -> list[dict]:
    rnd = random.Random(f"{seed}-{day.isoformat()}")
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        channel = rnd.choices(["POS", "ECOM", "ATM", "CONTACTLESS"], [40, 35, 10, 15])[0]
        ts = start + timedelta(seconds=rnd.randint(0, 86399))
        if channel == "ATM":
            merchant, mcc, amount = "", "6011", rnd.choice([500, 1000, 2000, 5000, 10000])
        else:
            mcc = rnd.choice(list(MCC))
            lo, hi = MCC[mcc]
            merchant, amount = f"M{rnd.randint(1, 400):05d}", round(rnd.uniform(lo, hi), 2)
        rows.append({
            "txn_id": f"T{day:%Y%m%d}{i:06d}",
            "account_id": f"AC{rnd.randint(1, 5000):07d}",
            "card_bin": rnd.choice(BINS),
            "merchant_id": merchant,
            "mcc": mcc,
            "channel": channel,
            "amount": round(amount * scale, 2),
            "currency": rnd.choice(CURRENCIES),
            "txn_ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "settlement_date": (ts.date() + timedelta(days=rnd.choice([0, 1, 1, 2]))).isoformat(),
        })
    for row in rows:
        if rnd.random() >= bad_rate:
            continue
        row["_defect"] = d = rnd.choice(DEFECTS)
        if d == "dup_txn_id":
            other = rnd.choice(rows)
            row["txn_id"], row["txn_ts"] = other["txn_id"], other["txn_ts"]   # exact replay of a record
            row.update({k: other[k] for k in COLUMNS})
        elif d == "null_account":
            row["account_id"] = ""
        elif d == "bad_amount":
            row["amount"] = rnd.choice([0, -1 * row["amount"]])
        elif d == "bad_currency":
            row["currency"] = rnd.choice(["XXX", "inr", "RS"])
        elif d == "bad_bin":
            row["card_bin"] = row["card_bin"][:5]
        elif d == "settle_before_txn":
            row["settlement_date"] = (date.fromisoformat(row["txn_ts"][:10]) - timedelta(days=1)).isoformat()
        elif d == "bad_mcc":
            row["mcc"] = "54A1"
    return rows


def write_csv(rows: list[dict], path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--date", required=True)
    p.add_argument("--rows", type=int, default=400)
    p.add_argument("--bad-rate", type=float, default=0.0)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    rows = make_rows(date.fromisoformat(a.date), a.rows, a.bad_rate, a.seed, a.scale)
    print(write_csv(rows, a.out), len(rows), "rows,", sum("_defect" in r for r in rows), "with defects")


if __name__ == "__main__":
    main()
