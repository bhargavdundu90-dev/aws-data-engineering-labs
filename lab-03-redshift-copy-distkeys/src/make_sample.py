"""Write sample_data/card_txn.csv: card transactions for a few hundred customers."""
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path


def make_rows(n=600, n_customers=150, seed=11):
    rng = random.Random(seed)
    start = date(2026, 10, 1)
    rows = []
    for i in range(1, n + 1):
        d = start + timedelta(days=rng.randrange(29))
        ts = datetime.combine(d, datetime.min.time()) + timedelta(seconds=rng.randrange(86400))
        rows.append([100000 + i, rng.randint(1, n_customers), rng.randint(1, 40), d.isoformat(),
                     ts.isoformat(sep=" "), f"{rng.uniform(50, 25000):.2f}",
                     rng.choices(["POS", "ECOM", "ATM"], [6, 3, 1])[0],
                     rng.choices(["SUCCESS", "DECLINED"], [19, 1])[0]])
    return rows


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "sample_data" / "card_txn.csv"
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["txn_id", "customer_id", "merchant_id", "txn_date", "txn_ts", "amount_inr", "channel", "status"])
        w.writerows(make_rows())
    print(f"wrote {out}")
