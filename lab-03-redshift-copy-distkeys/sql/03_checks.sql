-- Distribution skew and unsorted % per table (skew_rows close to 1.0 is good)
SELECT "table", diststyle, sortkey1, tbl_rows, skew_rows, unsorted, stats_off
FROM svv_table_info
WHERE schema = 'analytics'
ORDER BY skew_rows DESC;

-- Rows per slice for the fact table: a hot slice means a bad DISTKEY
SELECT slice, COUNT(*) AS blocks
FROM stv_blocklist b
JOIN stv_tbl_perm p ON b.tbl = p.id AND b.slice = p.slice
WHERE TRIM(p.name) = 'fact_card_txn'
GROUP BY slice ORDER BY slice;

-- Join must not redistribute: look for DS_DIST_NONE (good) vs DS_BCAST_INNER / DS_DIST_BOTH (bad)
EXPLAIN
SELECT c.segment, DATE_TRUNC('month', f.txn_date) AS month, SUM(f.amount_inr) AS spend
FROM analytics.fact_card_txn f
JOIN analytics.dim_customer c ON c.customer_id = f.customer_id
WHERE f.txn_date >= DATEADD(day, -30, CURRENT_DATE)
GROUP BY 1, 2;

-- Why did a COPY fail?
SELECT starttime, filename, line_number, colname, err_code, TRIM(err_reason) AS reason
FROM stl_load_errors
ORDER BY starttime DESC
LIMIT 20;
