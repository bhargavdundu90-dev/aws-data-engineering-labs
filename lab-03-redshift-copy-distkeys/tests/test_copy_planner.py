import csv
import gzip
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from copy_planner import (build_copy_sql, build_manifest, recommend_diststyle, slice_skew,
                          split_csv_for_copy, upload_manifest)

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "card_txn.csv"


def _rows(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def test_split_makes_multiple_of_slices_and_keeps_every_row(tmp_path):
    parts = split_csv_for_copy(SAMPLE, tmp_path, n_slices=4, files_per_slice=2)
    assert len(parts) == 8
    total, sizes = 0, []
    for p in parts:
        with gzip.open(p, "rt") as gz:
            lines = gz.read().splitlines()
        assert lines[0].startswith("txn_id,")  # header in every part
        sizes.append(len(lines) - 1)
        total += len(lines) - 1
    assert total == len(_rows(SAMPLE))
    assert max(sizes) - min(sizes) <= 1  # evenly sized parts


def test_low_cardinality_distkey_skews_badly():
    rows = _rows(SAMPLE)
    _, skew_customer = slice_skew([r["customer_id"] for r in rows], n_slices=8)
    per_slice, skew_channel = slice_skew([r["channel"] for r in rows], n_slices=8)
    assert skew_customer < 1.5
    assert skew_channel > 3
    assert sum(1 for c in per_slice if c) <= 3  # only 3 channels -> at most 3 busy slices


@pytest.mark.parametrize("rows,joins,keys,expected", [
    (40, "merchant_id", 40, "ALL"),
    (50_000_000, "customer_id", 2_000_000, "KEY(customer_id)"),
    (50_000_000, None, None, "EVEN"),
    (50_000_000, "channel", 3, "EVEN"),
])
def test_recommend_diststyle(rows, joins, keys, expected):
    assert recommend_diststyle(rows, joins, keys) == expected


def test_copy_sql_csv_with_manifest():
    sql = build_copy_sql("analytics.fact_card_txn", "s3://b/m/day.manifest",
                         "arn:aws:iam::123456789012:role/r", fmt="csv", manifest=True, gzip_files=True)
    assert "MANIFEST" in sql and "GZIP" in sql and "IGNOREHEADER 1" in sql
    assert sql.endswith(";")


def test_copy_sql_parquet_rejects_csv_options():
    sql = build_copy_sql("t", "s3://b/p/", "arn:aws:iam::1:role/r")
    assert "FORMAT AS PARQUET" in sql
    with pytest.raises(ValueError):
        build_copy_sql("t", "s3://b/p/", "arn:aws:iam::1:role/r", gzip_files=True)


@mock_aws
def test_manifest_from_s3_prefix(tmp_path):
    s3 = boto3.client("s3", region_name="ap-south-1")
    s3.create_bucket(Bucket="bfsi-landing-dev",
                     CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
    for p in split_csv_for_copy(SAMPLE, tmp_path, n_slices=2):
        s3.upload_file(str(p), "bfsi-landing-dev", f"card_txn/2026-10-29/{p.name}")
    s3.put_object(Bucket="bfsi-landing-dev", Key="card_txn/2026-10-29/_SUCCESS", Body=b"")

    manifest = build_manifest(s3, "bfsi-landing-dev", "card_txn/2026-10-29/", suffix=".csv.gz")
    assert len(manifest["entries"]) == 2
    assert all(e["mandatory"] and e["meta"]["content_length"] > 0 for e in manifest["entries"])

    uri = upload_manifest(s3, manifest, "bfsi-landing-dev", "manifests/card_txn_2026-10-29.manifest")
    body = s3.get_object(Bucket="bfsi-landing-dev", Key="manifests/card_txn_2026-10-29.manifest")["Body"].read()
    assert json.loads(body) == manifest and uri.endswith(".manifest")


@mock_aws
def test_manifest_empty_prefix_raises():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="empty")
    with pytest.raises(ValueError):
        build_manifest(s3, "empty", "nothing/")
