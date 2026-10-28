import sys
from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F, types as T

args = getResolvedOptions(sys.argv, ["JOB_NAME", "SOURCE_PATH", "TARGET_PATH"])
sc = SparkContext()
glue = GlueContext(sc)
spark = glue.spark_session
spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
job = Job(glue)
job.init(args["JOB_NAME"], args)

schema = T.StructType([
    T.StructField("txn_id", T.StringType(), False),
    T.StructField("account_id", T.StringType(), True),
    T.StructField("txn_ts", T.StringType(), True),
    T.StructField("amount", T.StringType(), True),
    T.StructField("currency", T.StringType(), True),
    T.StructField("product", T.StringType(), True),
])

raw = spark.read.option("header", True).schema(schema).csv(args["SOURCE_PATH"])

clean = (
    raw.withColumn("txn_ts", F.to_timestamp("txn_ts"))
       .withColumn("amount", F.col("amount").cast("decimal(18,2)"))
       .withColumn("currency", F.upper(F.trim("currency")))
       .withColumn("txn_date", F.to_date("txn_ts"))
)

# Data quality: drop bad rows, keep counts for the log
valid = clean.filter(F.col("txn_id").isNotNull() & F.col("txn_ts").isNotNull() & (F.col("amount") >= 0))
deduped = valid.dropDuplicates(["txn_id"])
print(f"raw={raw.count()} valid={valid.count()} final={deduped.count()}")

(deduped.write.mode("overwrite")
        .partitionBy("txn_date")
        .option("compression", "snappy")
        .parquet(args["TARGET_PATH"]))

job.commit()
