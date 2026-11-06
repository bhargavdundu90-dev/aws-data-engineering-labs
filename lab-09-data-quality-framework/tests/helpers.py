from datetime import date, datetime

from schema import TXN_SCHEMA


def good_row(i: int, **over):
    row = {
        "txn_id": f"T{i:06d}", "account_id": f"AC{i:07d}", "card_bin": "414720", "merchant_id": f"M{i:05d}",
        "mcc": "5411", "channel": "POS", "amount": 1000.0 + i, "currency": "INR",
        "txn_ts": datetime(2026, 11, 5, 10, 0, i % 60), "settlement_date": date(2026, 11, 6),
    }
    row.update(over)
    return row


def frame(spark, rows):
    cols = [f.name for f in TXN_SCHEMA.fields]
    return spark.createDataFrame([tuple(r[c] for c in cols) for r in rows], TXN_SCHEMA)
