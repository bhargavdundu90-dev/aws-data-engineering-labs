-- Card transactions table with partition projection.
-- No crawler and no MSCK REPAIR TABLE: Athena computes partition paths from the properties below.
CREATE EXTERNAL TABLE IF NOT EXISTS bfsi_curated.card_txn (
  `txn_id`            string,
  `card_id`           string,
  `customer_id`       string,
  `txn_ts`            timestamp,
  `amount`            decimal(12,2),
  `currency`          string,
  `merchant_id`       string,
  `merchant_category` string,
  `channel`           string
)
PARTITIONED BY (`region` string, `dt` string)
STORED AS PARQUET
LOCATION 's3://bfsi-lake-dev/curated/card_txn/'
TBLPROPERTIES (
  'projection.enabled'          = 'true',
  'projection.dt.type'          = 'date',
  'projection.dt.format'        = 'yyyy-MM-dd',
  'projection.dt.range'         = '2024-01-01,NOW',
  'projection.dt.interval'      = '1',
  'projection.dt.interval.unit' = 'DAYS',
  'projection.region.type'      = 'enum',
  'projection.region.values'    = 'south,west,north,east',
  'storage.location.template'   = 's3://bfsi-lake-dev/curated/card_txn/region=${region}/dt=${dt}/',
  'parquet.compression'         = 'SNAPPY'
);
