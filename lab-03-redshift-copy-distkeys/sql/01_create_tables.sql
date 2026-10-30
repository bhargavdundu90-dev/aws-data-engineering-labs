-- Card-transactions star schema on Redshift, tuned for the two hottest queries:
--   (a) spend per customer per month   -> fact joins dim_customer on customer_id
--   (b) last-N-days filters on txn_date -> range-restricted scans

-- Small, slowly changing dimensions: copy to every node so joins never move data.
CREATE TABLE IF NOT EXISTS analytics.dim_merchant (
    merchant_id     INTEGER      NOT NULL,
    merchant_name   VARCHAR(120) NOT NULL,
    mcc             CHAR(4)      NOT NULL,
    city            VARCHAR(40)
)
DISTSTYLE ALL
SORTKEY (merchant_id);

CREATE TABLE IF NOT EXISTS analytics.dim_date (
    date_key     DATE     NOT NULL,
    year_month   CHAR(7)  NOT NULL,
    is_weekend   BOOLEAN  NOT NULL
)
DISTSTYLE ALL
SORTKEY (date_key);

-- Large dimension that joins to the fact on customer_id: co-locate on the same key.
CREATE TABLE IF NOT EXISTS analytics.dim_customer (
    customer_id   BIGINT      NOT NULL,
    segment       VARCHAR(20) NOT NULL,   -- RETAIL / PREMIUM / NRI
    risk_tier     VARCHAR(10) NOT NULL,
    home_branch   VARCHAR(10)
)
DISTKEY (customer_id)
SORTKEY (customer_id);

-- Fact: DISTKEY matches dim_customer, so customer joins are DS_DIST_NONE.
-- Compound sort key starting with txn_date gives zone-map pruning on date ranges.
CREATE TABLE IF NOT EXISTS analytics.fact_card_txn (
    txn_id        BIGINT        NOT NULL,
    customer_id   BIGINT        NOT NULL,
    merchant_id   INTEGER       NOT NULL,
    txn_date      DATE          NOT NULL,
    txn_ts        TIMESTAMP     NOT NULL,
    amount_inr    DECIMAL(14,2) NOT NULL ENCODE az64,
    channel       VARCHAR(10)   NOT NULL ENCODE bytedict,  -- POS / ECOM / ATM
    status        VARCHAR(10)   NOT NULL ENCODE bytedict
)
DISTKEY (customer_id)
COMPOUND SORTKEY (txn_date, customer_id);
