-- 1) Parquet from the curated zone (preferred: columnar, schema-typed, no parsing options)
COPY analytics.fact_card_txn
FROM 's3://bfsi-curated-dev/card_txn/txn_date=2026-10-29/'
IAM_ROLE 'arn:aws:iam::<account-id>:role/redshift-copy-role'
FORMAT AS PARQUET;

-- 2) CSV through a manifest: loads exactly the listed files, fails if one is missing.
--    Files are gzip and split to a multiple of the cluster's slice count for parallel load.
COPY analytics.fact_card_txn
FROM 's3://bfsi-landing-dev/manifests/card_txn_2026-10-29.manifest'
IAM_ROLE 'arn:aws:iam::<account-id>:role/redshift-copy-role'
MANIFEST
CSV IGNOREHEADER 1
GZIP
DATEFORMAT 'YYYY-MM-DD'
TIMEFORMAT 'auto'
MAXERROR 0
COMPUPDATE OFF
STATUPDATE ON;

-- Small dimension loads
COPY analytics.dim_merchant
FROM 's3://bfsi-landing-dev/reference/merchants.csv'
IAM_ROLE 'arn:aws:iam::<account-id>:role/redshift-copy-role'
CSV IGNOREHEADER 1;

-- Idempotent reload of one day: stage, delete the day, insert, all in one transaction
BEGIN;
CREATE TEMP TABLE stage_card_txn (LIKE analytics.fact_card_txn);
COPY stage_card_txn
FROM 's3://bfsi-curated-dev/card_txn/txn_date=2026-10-29/'
IAM_ROLE 'arn:aws:iam::<account-id>:role/redshift-copy-role'
FORMAT AS PARQUET;
DELETE FROM analytics.fact_card_txn WHERE txn_date = '2026-10-29';
INSERT INTO analytics.fact_card_txn SELECT * FROM stage_card_txn;
END;
