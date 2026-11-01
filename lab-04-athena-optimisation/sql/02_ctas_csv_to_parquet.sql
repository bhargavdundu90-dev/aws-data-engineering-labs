-- One-off conversion of the raw CSV table into partitioned, compressed Parquet.
-- Partition columns must be the LAST columns in the SELECT, in PARTITIONED BY order.
-- A single CTAS / INSERT INTO can write at most 100 partitions; backfill larger ranges in chunks.
CREATE TABLE bfsi_curated.card_txn_backfill
WITH (
  format            = 'PARQUET',
  write_compression = 'SNAPPY',
  external_location = 's3://bfsi-lake-dev/curated/card_txn/',
  partitioned_by    = ARRAY['region', 'dt']
) AS
SELECT
  txn_id, card_id, customer_id,
  CAST(from_iso8601_timestamp(txn_ts) AS timestamp) AS txn_ts,
  CAST(amount AS decimal(12,2))                     AS amount,
  currency, merchant_id, merchant_category, channel,
  region,
  substr(txn_ts, 1, 10)                             AS dt
FROM bfsi_raw.card_txn_csv
WHERE substr(txn_ts, 1, 10) BETWEEN '2026-10-01' AND '2026-10-24';  -- 4 regions x 24 days = 96 partitions
