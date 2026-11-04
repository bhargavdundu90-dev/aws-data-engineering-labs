"""Write daily full snapshots of a loan-account table, the way the core system exports them.

Each day some accounts change: repo-rate linked rate resets (EMI moves with them), branch
transfers, status moving to SMA-1 / NPA, name or mobile corrections, new disbursals,
closures (account disappears from the extract) and the odd closed account that comes back.

    python src/make_snapshots.py --days 4 --accounts 12 --out sample_data/snapshots
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

PRODUCTS = {"HL": (8.40, 9.25), "PL": (10.75, 14.50), "AL": (8.90, 10.40)}
BRANCHES = ["HYD001", "BLR004", "MAA002", "VGA001", "PNQ003"]
STATUSES = ["ACTIVE", "SMA1", "NPA"]
FIELDS = ["account_id", "customer_name", "mobile", "product_code", "interest_rate", "emi_amount",
          "branch_code", "account_status"]


def emi(principal: float, annual_rate: float, months: int) -> float:
    r = annual_rate / 12 / 100
    return round(principal * r * (1 + r) ** months / ((1 + r) ** months - 1), 2)


def _new_account(rng, n):
    product = rng.choice(sorted(PRODUCTS))
    lo, hi = PRODUCTS[product]
    rate = round(rng.uniform(lo, hi), 2)
    principal = rng.choice([300000, 750000, 1500000, 4500000])
    return {"account_id": f"LN{n:06d}", "customer_name": f"Customer {n}",
            "mobile": f"9{rng.randrange(10**8, 10**9)}", "product_code": product,
            "interest_rate": rate, "_principal": principal,
            "emi_amount": emi(principal, rate, 120), "branch_code": rng.choice(BRANCHES),
            "account_status": "ACTIVE"}


def generate(days=4, accounts=12, seed=7, start=date(2026, 11, 1), change_rate=0.25):
    """Return [(snapshot_date, [rows])]; rows are dicts with FIELDS."""
    rng = random.Random(seed)
    live = {}
    closed = {}
    next_id = 1
    for _ in range(accounts):
        a = _new_account(rng, next_id)
        live[a["account_id"]] = a
        next_id += 1

    out = []
    for day in range(days):
        if day > 0:
            for acc_id in sorted(live):
                if rng.random() >= change_rate:
                    continue
                a = live[acc_id]
                kind = rng.choice(["rate", "branch", "status", "name", "mobile", "close"])
                if kind == "rate":
                    a["interest_rate"] = round(a["interest_rate"] + rng.choice([-0.25, 0.25, 0.50]), 2)
                    a["emi_amount"] = emi(a["_principal"], a["interest_rate"], 120)
                elif kind == "branch":
                    a["branch_code"] = rng.choice([b for b in BRANCHES if b != a["branch_code"]])
                elif kind == "status":
                    a["account_status"] = rng.choice([s for s in STATUSES if s != a["account_status"]])
                elif kind == "name":
                    a["customer_name"] = a["customer_name"].upper()  # data-entry correction
                elif kind == "mobile":
                    a["mobile"] = f"9{rng.randrange(10**8, 10**9)}"
                else:
                    closed[acc_id] = live.pop(acc_id)
            if closed and rng.random() < 0.5:  # a closure reversed by ops
                acc_id = rng.choice(sorted(closed))
                live[acc_id] = closed.pop(acc_id)
            for _ in range(rng.randint(0, 2)):  # new disbursals
                a = _new_account(rng, next_id)
                live[a["account_id"]] = a
                next_id += 1
        snap = [{k: a[k] for k in FIELDS} for _, a in sorted(live.items())]
        out.append((start + timedelta(days=day), snap))
    return out


def write(snapshots, folder) -> list[Path]:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for d, rows in snapshots:
        p = folder / f"loan_accounts_{d.isoformat()}.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({**r, "interest_rate": f"{r['interest_rate']:.2f}", "emi_amount": f"{r['emi_amount']:.2f}"})
        paths.append(p)
    return paths


def main(argv=None):
    p = argparse.ArgumentParser(description="Generate daily loan-account snapshots")
    p.add_argument("--days", type=int, default=4)
    p.add_argument("--accounts", type=int, default=12)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="sample_data/snapshots")
    a = p.parse_args(argv)
    for path in write(generate(a.days, a.accounts, a.seed), a.out):
        print(path)


if __name__ == "__main__":
    main()
