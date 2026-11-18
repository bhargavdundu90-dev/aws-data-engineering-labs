"""Lambda: S3 object-created event -> EMR Serverless StartJobRun.

Config comes from environment variables, so the handler has no hard-coded IDs.
"""
import hashlib
import os
import re
import urllib.parse


KEY_RE = re.compile(r"^landing/(?P<domain>[a-z_]+)/dt=(?P<dt>\d{4}-\d{2}-\d{2})/.+\.(csv|json|parquet)$")


def parse_records(event):
    """Yield (bucket, key) for each S3 record, URL-decoding keys."""
    for rec in event.get("Records", []):
        s3 = rec["s3"]
        yield s3["bucket"]["name"], urllib.parse.unquote_plus(s3["object"]["key"])


def build_job_request(bucket, key, env):
    """Return start_job_run kwargs, or None when the key is not an input file."""
    m = KEY_RE.match(key)
    if not m:
        return None
    domain, dt = m.group("domain"), m.group("dt")
    return {
        "applicationId": env["EMR_APPLICATION_ID"],
        "executionRoleArn": env["EMR_EXECUTION_ROLE_ARN"],
        "name": f"{domain}-{dt}",
        # idempotency: the same file re-delivered does not start a second run
        "clientToken": _token(bucket, key),
        "jobDriver": {
            "sparkSubmit": {
                "entryPoint": env["SPARK_ENTRYPOINT"],
                "entryPointArguments": ["--input", f"s3://{bucket}/{key}", "--domain", domain, "--dt", dt],
                "sparkSubmitParameters": "--conf spark.executor.memory=4g --conf spark.executor.cores=2",
            }
        },
        "tags": {"domain": domain, "dt": dt},
    }


def _token(bucket, key):
    return hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()[:32]


def handler(event, context=None, client=None):
    env = os.environ
    if client is None:
        import boto3

        client = boto3.client("emr-serverless")
    started, skipped = [], []
    for bucket, key in parse_records(event):
        req = build_job_request(bucket, key, env)
        if req is None:
            skipped.append(key)
            continue
        started.append(client.start_job_run(**req)["jobRunId"])
    return {"started": started, "skipped": skipped}
