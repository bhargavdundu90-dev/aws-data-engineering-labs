# AWS Data Engineering Labs

Hands-on labs by **Dundu Bhargav**, AWS Data Engineer (4+ yrs, BFSI) · AWS Certified Data Engineer – Associate.
Each lab is a small, runnable pipeline using AWS Glue, EMR, PySpark, Lambda, S3, Athena and Terraform.

LinkedIn: https://www.linkedin.com/in/bhargavdundu

## Flagship projects
| Project | What it does | Stack |
|---------|--------------|-------|
| [kafka-spark-realtime-streaming](https://github.com/bhargavdundu90-dev/kafka-spark-realtime-streaming) | Real-time card/UPI transaction streaming with per-card fraud signals, landing to S3/Parquet | Kafka, Spark Structured Streaming, S3, MSK/EMR Serverless, Glue, Athena, Terraform |

## Labs
| # | Lab | Services |
|---|-----|----------|
| 01 | [CSV → partitioned Parquet with Glue + Athena](lab-01-s3-glue-athena/) | S3, Glue (PySpark), Glue Catalog, Athena |
| 02 | [Event-driven ingestion: S3 → Lambda → curated/quarantine](lab-02-lambda-s3-events/) | S3 events, Lambda, moto tests |
| 03 | [Redshift COPY, DISTKEY and SORTKEY design](lab-03-redshift-copy-distkeys/) | Redshift, S3 manifests, COPY, moto tests |
| 04 | [Athena partition projection and cost tuning](lab-04-athena-optimisation/) | Athena, Glue Catalog, S3, Parquet, CTAS |
| 05 | [Step Functions ETL orchestration: validate → Glue → Athena DQ → SNS](lab-05-step-functions-etl/) | Step Functions, Lambda, Glue, Athena, SNS, Terraform, moto tests |
| 06 | [LLM data-quality checks on KYC applications (rules + Bedrock)](lab-06-bedrock-llm-dq/) | Bedrock Converse, Python, PII masking, botocore Stubber tests |
| 07 | [SCD Type 2 from daily full snapshots (loan accounts)](lab-07-pyspark-scd2/) | PySpark, Iceberg MERGE, Glue, Athena, local Spark tests |
| 08 | [Kinesis Data Streams → Lambda → S3 for UPI/card transactions](lab-08-kinesis-streaming/) | Kinesis, Lambda (partial batch response), S3, SQS DLQ, Terraform, moto tests |
| 09 | [Data-quality framework for card transactions: YAML checks, quarantine, anomaly checks, DQDL export](lab-09-data-quality-framework/) | PySpark, Glue Data Quality (DQDL), Deequ / Great Expectations mapping, local Spark tests |
| 10 | [Lake Formation governance: LF-tags, PII columns, row filters and grant audit](lab-10-lake-formation/) | Lake Formation (LF-TBAC, data cell filters), Glue Catalog, Athena, Terraform, botocore Stubber tests |
