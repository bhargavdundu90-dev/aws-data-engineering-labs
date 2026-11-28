# Lab 17: Athena "Access Denied" troubleshooting (S3 + IAM + Lake Formation)

## Scenario
An analyst queries a loan-repayments table in Athena and gets Access Denied. Several layers can say no, so check them in a fixed order.

| Order | Layer | Typical cause |
|---|---|---|
| 1 | Lake Formation grant | no SELECT on table / DESCRIBE on database |
| 2 | IAM identity policy | missing athena:StartQueryExecution |
| 3 | S3 bucket policy | explicit Deny |
| 4 | KMS key policy | no kms:Decrypt |
| 5 | Resource link | grant given on link, not on target table |

## How to run (local, no AWS)
```
pip install pytest
python -m pytest -q lab-17-access-troubleshooting
```
