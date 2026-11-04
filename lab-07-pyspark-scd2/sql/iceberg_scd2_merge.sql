-- Same SCD2 load on an Iceberg table in Athena (engine v3) or Glue Spark SQL.
-- stg_loan_accounts = today's full snapshot with t2_hash already computed
-- (sha2 of the Type 2 columns, same as scd2_snapshot.row_hash). :snapshot_date is the load date as
-- 'YYYY-MM-DD', filled in by the job before it submits the statements.

-- 1) close current versions whose Type 2 columns changed, and accounts missing from the snapshot
MERGE INTO dim_loan_account d
USING (
    SELECT c.account_id, s.t2_hash AS new_hash
    FROM dim_loan_account c
    LEFT JOIN stg_loan_accounts s ON s.account_id = c.account_id
    WHERE c.is_current
) x
ON d.account_id = x.account_id AND d.is_current
WHEN MATCHED AND (x.new_hash IS NULL OR x.new_hash <> d.t2_hash) THEN
    UPDATE SET effective_to = DATE :snapshot_date, is_current = false;

-- 2) open a version for every snapshot row that now has no current version
--    (new accounts, changed accounts just closed in step 1, reopened accounts)
INSERT INTO dim_loan_account
SELECT substr(to_hex(sha256(to_utf8(s.account_id || ':' || :snapshot_date))), 1, 16) AS account_sk,
       s.account_id, s.product_code, s.interest_rate, s.emi_amount, s.branch_code, s.account_status,
       s.customer_name, s.mobile,
       DATE :snapshot_date AS effective_from, DATE '9999-12-31' AS effective_to, true AS is_current,
       CASE WHEN EXISTS (SELECT 1 FROM dim_loan_account h
                         WHERE h.account_id = s.account_id AND h.effective_to = DATE :snapshot_date) THEN 'update'
            WHEN EXISTS (SELECT 1 FROM dim_loan_account h WHERE h.account_id = s.account_id) THEN 'reopen'
            ELSE 'insert' END AS change_type,
       s.t2_hash
FROM stg_loan_accounts s
LEFT JOIN dim_loan_account c ON c.account_id = s.account_id AND c.is_current
WHERE c.account_id IS NULL;

-- 3) Type 1 corrections on every version of accounts in the snapshot
MERGE INTO dim_loan_account d
USING stg_loan_accounts s
ON d.account_id = s.account_id
WHEN MATCHED AND (d.customer_name IS DISTINCT FROM s.customer_name OR d.mobile IS DISTINCT FROM s.mobile) THEN
    UPDATE SET customer_name = s.customer_name, mobile = s.mobile;

-- point-in-time: which rate and status did each loan have on the RBI reporting date?
SELECT account_id, interest_rate, account_status
FROM dim_loan_account
WHERE DATE '2026-11-03' >= effective_from AND DATE '2026-11-03' < effective_to;
