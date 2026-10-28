# Lab 01 – CSV to Partitioned Parquet with AWS Glue (PySpark) + Athena

**Goal:** Ingest raw CSV transactions from S3, clean them with a Glue PySpark job, write Snappy-compressed Parquet partitioned by date, and query with Athena.

## Architecture
`s3://<bucket>/raw/transactions/*.csv` → **Glue job (PySpark)** → `s3://<bucket>/curated/transactions/txn_date=YYYY-MM-DD/` (Parquet) → **Glue Data Catalog** → **Athena**

## What it shows
- Schema enforcement and type casting in PySpark
- Basic data quality checks (nulls, duplicates, negative amounts)
- Hive-style partitioning + Parquet/Snappy to cut Athena scan cost
- Idempotent writes with dynamic partition overwrite

## Files
- `glue_job.py` – the Glue PySpark job
- `athena_queries.sql` – table DDL and sample queries
- `sample_data/transactions.csv` – small test file

## Run
1. Upload `sample_data/transactions.csv` to `s3://<bucket>/raw/transactions/`
2. Create a Glue job (Glue 4.0, Python 3) with `glue_job.py`; job params `--SOURCE_PATH` and `--TARGET_PATH`
3. Run the DDL in `athena_queries.sql`, then `MSCK REPAIR TABLE`
