"""Order the checks for an Athena 'Access Denied' on an S3-backed table.

Pure functions over plain dicts so the logic runs locally with no AWS calls.
"""
from dataclasses import dataclass, field

CHECK_ORDER = [
    "lake_formation_grant",
    "iam_identity_policy",
    "s3_bucket_policy",
    "kms_key_policy",
    "resource_link_target",
]


@dataclass
class Finding:
    check: str
    ok: bool
    hint: str = ""


@dataclass
class Report:
    findings: list = field(default_factory=list)

    @property
    def first_failure(self):
        return next((f for f in self.findings if not f.ok), None)


def _allows(statements, action):
    allowed = False
    for s in statements:
        acts = s.get("Action", [])
        acts = [acts] if isinstance(acts, str) else acts
        match = any(a == "*" or a == action or (a.endswith("*") and action.startswith(a[:-1])) for a in acts)
        if not match:
            continue
        if s.get("Effect") == "Deny":
            return False
        if s.get("Effect") == "Allow":
            allowed = True
    return allowed


def diagnose(ctx):
    """ctx keys: lf_grants (set of perms), iam_statements, bucket_statements,
    kms_statements (None if bucket not KMS encrypted), is_resource_link,
    link_target_grants."""
    rep = Report()
    lf = set(ctx.get("lf_grants", ()))
    rep.findings.append(Finding(
        "lake_formation_grant", "SELECT" in lf,
        "" if "SELECT" in lf else "Grant SELECT on the table (and DESCRIBE on the database) in Lake Formation"))
    iam_ok = _allows(ctx.get("iam_statements", []), "s3:GetObject") and _allows(
        ctx.get("iam_statements", []), "athena:StartQueryExecution")
    rep.findings.append(Finding("iam_identity_policy", iam_ok,
                                "" if iam_ok else "Role needs athena:StartQueryExecution; S3 access is via LF credential vending"))
    b = ctx.get("bucket_statements", [])
    b_ok = not b or not any(s.get("Effect") == "Deny" for s in b) or _allows(b, "s3:GetObject")
    rep.findings.append(Finding("s3_bucket_policy", b_ok, "" if b_ok else "Bucket policy has an explicit Deny"))
    k = ctx.get("kms_statements")
    k_ok = k is None or _allows(k, "kms:Decrypt")
    rep.findings.append(Finding("kms_key_policy", k_ok, "" if k_ok else "Key policy must allow kms:Decrypt"))
    if ctx.get("is_resource_link"):
        t = "SELECT" in set(ctx.get("link_target_grants", ()))
        rep.findings.append(Finding("resource_link_target", t,
                                    "" if t else "Grant on the resource link is not enough; grant on the target table"))
    return rep
