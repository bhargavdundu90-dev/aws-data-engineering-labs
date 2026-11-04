"""SCD Type 2 (with Type 1 columns) from daily full snapshots, in PySpark.

The source system here has no change log: every night it exports the whole loan-account
table as a CSV. To keep history we compare each snapshot with the current rows of the
dimension and work out what changed ourselves.

Column roles
  * Type 2 (keep history): product_code, interest_rate, emi_amount, branch_code, account_status
    -> a change closes the current version and opens a new one
  * Type 1 (overwrite):    customer_name, mobile
    -> a correction is applied to every version of the account, no new version
  * account missing from the snapshot -> the current version is closed (account closed
    or purged at source); if it shows up again later it gets a new version

Validity is half-open by snapshot date: [effective_from, effective_to). The open end is
9999-12-31.

Run locally:
    python src/scd2_snapshot.py --snapshots sample_data/snapshots --out output/dim_loan_account
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

KEY = "account_id"
TYPE2 = ["product_code", "interest_rate", "emi_amount", "branch_code", "account_status"]
TYPE1 = ["customer_name", "mobile"]
OPEN_END = date(9999, 12, 31)

SNAPSHOT_SCHEMA = T.StructType([
    T.StructField("account_id", T.StringType(), False),
    T.StructField("customer_name", T.StringType()),
    T.StructField("mobile", T.StringType()),
    T.StructField("product_code", T.StringType()),
    T.StructField("interest_rate", T.DecimalType(5, 2)),
    T.StructField("emi_amount", T.DecimalType(12, 2)),
    T.StructField("branch_code", T.StringType()),
    T.StructField("account_status", T.StringType()),
])

DIM_COLUMNS = ["account_sk", KEY, *TYPE2, *TYPE1, "effective_from", "effective_to", "is_current",
               "change_type", "t2_hash"]


class SnapshotError(ValueError):
    pass


def row_hash(cols: list[str]):
    # "~" for null so that NULL -> "" counts as a change; decimals cast to string keep their scale
    return F.sha2(F.concat_ws("|", *[F.coalesce(F.col(c).cast("string"), F.lit("~")) for c in cols]), 256)


def read_snapshot(spark: SparkSession, path: str) -> DataFrame:
    return spark.read.option("header", True).schema(SNAPSHOT_SCHEMA).csv(path)


def empty_dim(spark: SparkSession) -> DataFrame:
    schema = T.StructType([T.StructField("account_sk", T.StringType())]
                          + list(SNAPSHOT_SCHEMA.fields[:1])
                          + [f for f in SNAPSHOT_SCHEMA.fields if f.name in TYPE2]
                          + [f for f in SNAPSHOT_SCHEMA.fields if f.name in TYPE1]
                          + [T.StructField("effective_from", T.DateType()),
                             T.StructField("effective_to", T.DateType()),
                             T.StructField("is_current", T.BooleanType()),
                             T.StructField("change_type", T.StringType()),
                             T.StructField("t2_hash", T.StringType())])
    return spark.createDataFrame([], schema).select(*DIM_COLUMNS)


def validate_snapshot(snapshot: DataFrame) -> None:
    """Fail before touching the dimension: a bad extract must not close half the book."""
    stats = snapshot.agg(F.count("*").alias("rows"),
                         F.countDistinct(KEY).alias("keys"),
                         F.sum(F.col(KEY).isNull().cast("int")).alias("null_keys")).first()
    if stats["rows"] == 0:
        raise SnapshotError("empty snapshot: refusing to close every account")
    if stats["null_keys"]:
        raise SnapshotError(f"{stats['null_keys']} rows without {KEY}")
    if stats["keys"] != stats["rows"]:
        raise SnapshotError(f"duplicate {KEY}: {stats['rows']} rows but {stats['keys']} distinct keys")


def apply_snapshot(dim: DataFrame, snapshot: DataFrame, snapshot_date: date) -> DataFrame:
    """Return the dimension after loading one full snapshot taken on `snapshot_date`."""
    validate_snapshot(snapshot)
    last = dim.agg(F.max("effective_from")).first()[0]
    if last is not None and snapshot_date < last:
        raise SnapshotError(f"snapshot {snapshot_date} is older than the last load {last}; "
                            "reload from that date instead of applying it out of order")

    d = F.lit(snapshot_date).cast("date")
    snap = snapshot.withColumn("t2_hash", row_hash(TYPE2))
    current = dim.where("is_current")
    closed = dim.where(~F.col("is_current"))

    j = (current.alias("c").join(snap.alias("s"), F.col(f"c.{KEY}") == F.col(f"s.{KEY}"), "full_outer")
         .withColumn("_key", F.coalesce(F.col(f"c.{KEY}"), F.col(f"s.{KEY}")))
         .withColumn("_action",
                     F.when(F.col(f"c.{KEY}").isNull(), "insert")
                      .when(F.col(f"s.{KEY}").isNull(), "delete")
                      .when(F.col("c.t2_hash") != F.col("s.t2_hash"), "change")
                      .otherwise("same")))

    def from_current(effective_to=None, is_current=None):
        cols = [F.col(f"c.{c}").alias(c) for c in DIM_COLUMNS]
        if effective_to is not None:
            cols[DIM_COLUMNS.index("effective_to")] = effective_to.alias("effective_to")
            cols[DIM_COLUMNS.index("is_current")] = F.lit(is_current).alias("is_current")
        return cols

    def new_version(change_type):
        return [F.sha2(F.concat_ws(":", F.col(f"s.{KEY}"), d.cast("string")), 256).substr(1, 16).alias("account_sk"),
                F.col(f"s.{KEY}").alias(KEY),
                *[F.col(f"s.{c}").alias(c) for c in TYPE2 + TYPE1],
                d.alias("effective_from"), F.lit(OPEN_END).alias("effective_to"),
                F.lit(True).alias("is_current"), change_type.alias("change_type"), F.col("s.t2_hash")]

    kept = j.where("_action = 'same'").select(*from_current())
    ended = j.where("_action IN ('change', 'delete')").select(*from_current(d, False))
    opened = j.where("_action IN ('insert', 'change')").select(
        *new_version(F.when(F.col("_action") == "change", "update").otherwise("insert")))

    # an account that existed before, was closed and comes back is a "reopen", not a brand-new insert
    seen_before = closed.select(KEY).distinct().withColumn("_seen", F.lit(True))
    opened = (opened.join(seen_before, KEY, "left")
              .withColumn("change_type", F.when((F.col("change_type") == "insert") & F.col("_seen"), "reopen")
                          .otherwise(F.col("change_type")))
              .drop("_seen"))

    result = closed.select(*DIM_COLUMNS).unionByName(kept).unionByName(ended).unionByName(opened.select(*DIM_COLUMNS))
    return apply_type1(result, snapshot)


def apply_type1(dim: DataFrame, snapshot: DataFrame) -> DataFrame:
    """Overwrite Type 1 columns on every version of accounts present in the snapshot."""
    t1 = snapshot.select(KEY, *[F.col(c).alias(f"_new_{c}") for c in TYPE1], F.lit(True).alias("_in_snap"))
    out = dim.join(t1, KEY, "left")
    for c in TYPE1:
        # account not in today's snapshot (closed): keep the last known value.
        # A null in the snapshot is still applied: clearing a wrong mobile is a correction too.
        out = out.withColumn(c, F.when(F.col("_in_snap"), F.col(f"_new_{c}")).otherwise(F.col(c)))
    return out.select(*DIM_COLUMNS)


def as_of(dim: DataFrame, on: date) -> DataFrame:
    """Point-in-time view: the version of each account that was valid on `on`."""
    d = F.lit(on).cast("date")
    return dim.where((F.col("effective_from") <= d) & (d < F.col("effective_to")))


SNAPSHOT_FILE = re.compile(r"loan_accounts_(\d{4}-\d{2}-\d{2})\.csv$")


def snapshot_files(folder) -> list[tuple[date, str]]:
    found = []
    for p in sorted(Path(folder).glob("*.csv")):
        m = SNAPSHOT_FILE.search(p.name)
        if m:
            found.append((date.fromisoformat(m.group(1)), str(p)))
    return found


def load_all(spark: SparkSession, folder, dim: DataFrame | None = None) -> DataFrame:
    dim = dim if dim is not None else empty_dim(spark)
    for snap_date, path in snapshot_files(folder):
        # localCheckpoint cuts the lineage; without it every day re-plans all the previous days
        dim = apply_snapshot(dim, read_snapshot(spark, path), snap_date).localCheckpoint()
    return dim


def main(argv=None):
    p = argparse.ArgumentParser(description="Load daily loan-account snapshots into an SCD2 dimension")
    p.add_argument("--snapshots", default="sample_data/snapshots")
    p.add_argument("--out", default="output/dim_loan_account")
    a = p.parse_args(argv)
    spark = (SparkSession.builder.master("local[2]").appName("lab07-scd2")
             .config("spark.sql.session.timeZone", "UTC").getOrCreate())
    dim = load_all(spark, a.snapshots)
    dim.write.mode("overwrite").parquet(a.out)
    dim.groupBy("change_type", "is_current").count().orderBy("change_type", "is_current").show()
    spark.stop()


if __name__ == "__main__":
    main()
