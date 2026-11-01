-- 1) BAD: SELECT * with no partition filter -> scans every column of every day
SELECT * FROM bfsi_curated.card_txn LIMIT 100;   -- LIMIT does not reduce bytes scanned on its own

-- 2) GOOD: filter on the partition columns with plain literals, select only needed columns
SELECT merchant_category, SUM(amount) AS spend
FROM bfsi_curated.card_txn
WHERE region = 'south'
  AND dt = '2026-10-15'
GROUP BY merchant_category;

-- 3) BAD: a function on the partition column hides the literal value from projection
SELECT COUNT(*) FROM bfsi_curated.card_txn
WHERE date_format(date_parse(dt, '%Y-%m-%d'), '%Y-%m') = '2026-10';

-- 3b) GOOD: same question as a range on the raw string
SELECT COUNT(*) FROM bfsi_curated.card_txn
WHERE dt BETWEEN '2026-10-01' AND '2026-10-31';

-- 4) Approximate distinct for dashboards: far less memory than COUNT(DISTINCT)
SELECT dt, approx_distinct(card_id) AS active_cards
FROM bfsi_curated.card_txn
WHERE dt BETWEEN '2026-10-01' AND '2026-10-07'
GROUP BY dt;

-- 5) Check what a query really scanned: Athena console "Data scanned", or
--    aws athena get-query-execution --query-execution-id <id> \
--      --query 'QueryExecution.Statistics.DataScannedInBytes'
