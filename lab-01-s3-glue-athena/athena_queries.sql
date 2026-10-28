CREATE EXTERNAL TABLE IF NOT EXISTS labs.transactions (
  txn_id string, account_id string, txn_ts timestamp,
  amount decimal(18,2), currency string, product string
)
PARTITIONED BY (txn_date date)
STORED AS PARQUET
LOCATION 's3://<bucket>/curated/transactions/';

MSCK REPAIR TABLE labs.transactions;

-- Partition pruning: scans only one day's data
SELECT product, SUM(amount) AS total
FROM labs.transactions
WHERE txn_date = DATE '2026-10-01'
GROUP BY product ORDER BY total DESC;
