# Lab 13: Lambda-triggered EMR Serverless jobs

**Scenario (BFSI):** card-transaction files land in `landing/<domain>/dt=YYYY-MM-DD/`. A Lambda starts one EMR Serverless Spark job per file, passing the input path, domain and date as arguments.

| Concern | Choice |
|---|---|
| Trigger | S3 ObjectCreated event notification |
| Idempotency | `clientToken` = SHA-256 of bucket/key, so a re-delivered event does not start a second run |
| Filtering | Only `landing/<domain>/dt=.../*.csv|json|parquet`; markers like `_SUCCESS` are skipped |
| Config | Application ID, role ARN and entrypoint via environment variables |

```mermaid
flowchart LR
  S3[S3 landing] -->|ObjectCreated| L[Lambda]
  L -->|StartJobRun| E[EMR Serverless]
  E --> P[(Parquet curated)]
```

## How to run (local, no AWS)
```bash
pip install pytest
cd lab-13-lambda-emr-serverless && python -m pytest -q tests
```
