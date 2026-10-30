"""Helpers for loading Redshift efficiently and checking distribution choices.

- split_csv_for_copy: split one big CSV into N gzip parts (N = multiple of slices)
- build_manifest / upload_manifest: COPY manifest from an S3 prefix
- build_copy_sql: COPY statement for Parquet or CSV (+ manifest)
- slice_skew: simulate how rows spread over slices for a candidate DISTKEY
- recommend_diststyle: rule of thumb for ALL / KEY / EVEN
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from collections import Counter
from pathlib import Path


def split_csv_for_copy(src, out_dir, n_slices, files_per_slice=1, prefix="part"):
    """Round-robin the rows of `src` into n_slices * files_per_slice gzip files.

    COPY loads one file per slice at a time, so a single large file uses one
    slice while the rest sit idle. Equal-sized parts keep every slice busy.
    Header is repeated in each part (use IGNOREHEADER 1).
    """
    n_files = n_slices * files_per_slice
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(src, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        buffers = [io.StringIO() for _ in range(n_files)]
        writers = [csv.writer(b, lineterminator="\n") for b in buffers]
        for w in writers:
            w.writerow(header)
        for i, row in enumerate(reader):
            writers[i % n_files].writerow(row)
    paths = []
    for i, buf in enumerate(buffers):
        p = out_dir / f"{prefix}_{i:04d}.csv.gz"
        with gzip.open(p, "wt", encoding="utf-8", newline="") as gz:
            gz.write(buf.getvalue())
        paths.append(p)
    return paths


def build_manifest(s3, bucket, prefix, suffix=""):
    """Manifest listing every object under prefix; mandatory=true makes COPY fail on a missing file."""
    entries = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(suffix) and not obj["Key"].endswith(".manifest"):
                entries.append({"url": f"s3://{bucket}/{obj['Key']}", "mandatory": True,
                                "meta": {"content_length": obj["Size"]}})
    if not entries:
        raise ValueError(f"no files under s3://{bucket}/{prefix}")
    return {"entries": sorted(entries, key=lambda e: e["url"])}


def upload_manifest(s3, manifest, bucket, key):
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(manifest, indent=2).encode())
    return f"s3://{bucket}/{key}"


def build_copy_sql(table, s3_uri, iam_role, fmt="parquet", manifest=False, gzip_files=False,
                   ignore_header=True, max_error=0):
    lines = [f"COPY {table}", f"FROM '{s3_uri}'", f"IAM_ROLE '{iam_role}'"]
    fmt = fmt.lower()
    if fmt == "parquet":
        if manifest or gzip_files:
            # Parquet is already compressed per column chunk; manifest works but keep the rule simple here
            raise ValueError("use plain prefix COPY for Parquet")
        lines.append("FORMAT AS PARQUET")
    elif fmt == "csv":
        if manifest:
            lines.append("MANIFEST")
        lines.append("CSV IGNOREHEADER 1" if ignore_header else "CSV")
        if gzip_files:
            lines.append("GZIP")
        lines += ["TIMEFORMAT 'auto'", f"MAXERROR {max_error}", "COMPUPDATE OFF", "STATUPDATE ON"]
    else:
        raise ValueError(f"unsupported format {fmt}")
    return "\n".join(lines) + ";"


def _slice_for(value, n_slices):
    digest = hashlib.md5(str(value).encode()).hexdigest()
    return int(digest, 16) % n_slices


def slice_skew(values, n_slices):
    """Return (rows_per_slice, skew) where skew = max slice / average slice.

    Redshift hashes the DISTKEY to pick a slice. A low-cardinality key
    (channel, status) can only fill as many slices as it has distinct values.
    """
    counts = Counter(_slice_for(v, n_slices) for v in values)
    per_slice = [counts.get(i, 0) for i in range(n_slices)]
    avg = sum(per_slice) / n_slices
    return per_slice, round(max(per_slice) / avg, 2) if avg else 0.0


def recommend_diststyle(row_count, joins_on=None, distinct_join_keys=None, all_threshold=3_000_000):
    """Rule of thumb used when designing the tables in sql/01_create_tables.sql."""
    if row_count <= all_threshold and joins_on:
        return "ALL"
    if joins_on and distinct_join_keys and distinct_join_keys >= 1000:
        return f"KEY({joins_on})"
    return "EVEN"
