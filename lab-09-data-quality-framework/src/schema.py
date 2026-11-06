from pyspark.sql.types import DoubleType, StringType, StructField, StructType, TimestampType, DateType

# explicit schema: inferring types on a bad file would silently turn amount into a string column
TXN_SCHEMA = StructType([
    StructField("txn_id", StringType()),
    StructField("account_id", StringType()),
    StructField("card_bin", StringType()),
    StructField("merchant_id", StringType()),
    StructField("mcc", StringType()),
    StructField("channel", StringType()),
    StructField("amount", DoubleType()),
    StructField("currency", StringType()),
    StructField("txn_ts", TimestampType()),
    StructField("settlement_date", DateType()),
])
COLUMNS = [f.name for f in TXN_SCHEMA.fields]
