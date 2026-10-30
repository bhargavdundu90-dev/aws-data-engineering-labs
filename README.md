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
