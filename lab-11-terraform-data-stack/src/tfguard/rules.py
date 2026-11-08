"""Guardrails every data-stack plan must pass before `terraform apply`.

Each rule takes the parsed plan and an env config and returns findings. Errors block the apply,
warnings are printed in the pull request but do not fail the pipeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .plan import Plan

REQUIRED_TAGS = ("owner", "cost_center", "data_classification", "environment")

# resources whose destroy or replace loses data or breaks consumers
PROTECTED_TYPES = {
    "aws_s3_bucket",
    "aws_kms_key",
    "aws_glue_catalog_database",
    "aws_glue_catalog_table",
    "aws_dynamodb_table",
}

# taggable types we check; data sources and sub-resources (bucket versioning etc.) carry no tags
TAGGABLE_TYPES = {
    "aws_s3_bucket", "aws_kms_key", "aws_glue_job", "aws_glue_catalog_database", "aws_iam_role",
    "aws_athena_workgroup", "aws_cloudwatch_log_group", "aws_dynamodb_table",
}

ENV_LIMITS = {
    "dev": {"max_glue_workers": 4, "allow_destroy": True},
    "prod": {"max_glue_workers": 20, "allow_destroy": False},
}

MIN_GLUE_VERSION = 4.0


@dataclass
class Finding:
    rule: str
    severity: str   # error | warn
    address: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper():5}] {self.rule}: {self.address} - {self.message}"


def _err(rule, addr, msg):
    return Finding(rule, "error", addr, msg)


def _warn(rule, addr, msg):
    return Finding(rule, "warn", addr, msg)


def check_bucket_guardrails(plan: Plan, env: str) -> list[Finding]:
    """Every bucket is created in a module that also blocks public access, encrypts with KMS and versions it.

    In a plan the bucket ids are unknown until apply, so the companion resources are matched by
    module: the lake_bucket module always creates them side by side. A bucket declared outside a
    module that does this is exactly what the rule is meant to catch.
    """
    out = []
    for b in plan.by_type("aws_s3_bucket"):
        mod = b.module
        pab = plan.in_module(mod, "aws_s3_bucket_public_access_block")
        sse = plan.in_module(mod, "aws_s3_bucket_server_side_encryption_configuration")
        ver = plan.in_module(mod, "aws_s3_bucket_versioning")
        if not pab:
            out.append(_err("bucket_public_access", b.address, "no public access block next to the bucket"))
        else:
            flags = ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")
            off = [f for f in flags if pab[0].after.get(f) is not True]
            if off:
                out.append(_err("bucket_public_access", b.address, f"public access flags not true: {', '.join(off)}"))
        if not sse:
            out.append(_err("bucket_encryption", b.address, "no server-side encryption configuration"))
        else:
            algo = _sse_algorithm(sse[0].after)
            if algo != "aws:kms":
                out.append(_err("bucket_encryption", b.address, f"SSE is {algo or 'unset'}, lake buckets use aws:kms"))
        if not ver:
            out.append(_warn("bucket_versioning", b.address, "versioning not enabled"))
        if b.after.get("force_destroy") and env == "prod":
            out.append(_err("bucket_force_destroy", b.address, "force_destroy=true in prod"))
    return out


def _sse_algorithm(after: dict) -> str | None:
    for rule in after.get("rule") or []:
        for d in rule.get("apply_server_side_encryption_by_default") or []:
            return d.get("sse_algorithm")
    return None


def check_required_tags(plan: Plan, env: str) -> list[Finding]:
    """Cost and compliance reports group by these tags; tags_all includes provider default_tags."""
    out = []
    for c in plan.changes:
        if c.type not in TAGGABLE_TYPES or c.is_delete_only or c.is_noop:
            continue
        tags = c.after.get("tags_all") or c.after.get("tags") or {}
        missing = [t for t in REQUIRED_TAGS if not tags.get(t)]
        if missing:
            out.append(_err("required_tags", c.address, f"missing tags: {', '.join(missing)}"))
        elif tags.get("environment") != env:
            out.append(_err("required_tags", c.address,
                            f"environment tag is {tags.get('environment')!r} but plan is for {env!r}"))
    return out


def _statements(policy_json: str) -> list[dict]:
    try:
        doc = json.loads(policy_json)
    except (TypeError, json.JSONDecodeError):
        return []
    st = doc.get("Statement", [])
    return [st] if isinstance(st, dict) else st


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def check_iam_wildcards(plan: Plan, env: str) -> list[Finding]:
    """No Allow statement with Action "*" / "service:*" on Resource "*".

    Deny statements are skipped: the TLS-only bucket policy is a Deny on s3:* by design.
    """
    out = []
    for c in plan.changes:
        if c.type not in ("aws_iam_role_policy", "aws_iam_policy", "aws_s3_bucket_policy") or c.is_delete_only:
            continue
        policy = c.after.get("policy")
        if not policy:
            continue  # unknown until apply (built from other resources); covered by module review
        for st in _statements(policy):
            if st.get("Effect") != "Allow":
                continue
            actions = _as_list(st.get("Action"))
            resources = _as_list(st.get("Resource"))
            wild_action = [a for a in actions if a == "*" or a.endswith(":*")]
            if wild_action and "*" in resources:
                out.append(_err("iam_wildcard", c.address,
                                f"Allow {', '.join(wild_action)} on Resource * (sid {st.get('Sid', '-')})"))
            elif wild_action:
                out.append(_warn("iam_wildcard", c.address,
                                 f"wildcard action {', '.join(wild_action)} (sid {st.get('Sid', '-')})"))
            elif "*" in resources:
                out.append(_warn("iam_wildcard", c.address, f"Resource * (sid {st.get('Sid', '-')})"))
    return out


def check_destructive(plan: Plan, env: str, allow: set[str] | None = None) -> list[Finding]:
    """Destroying or replacing a bucket, key or catalog object needs an explicit allow in prod."""
    allow = allow or set()
    limits = ENV_LIMITS[env]
    out = []
    for c in plan.changes:
        if c.type not in PROTECTED_TYPES or not c.is_delete:
            continue
        verb = "replace" if len(c.actions) > 1 else "destroy"
        if c.address in allow:
            out.append(_warn("destructive_change", c.address, f"{verb} allowed by --allow-destroy"))
        elif limits["allow_destroy"]:
            out.append(_warn("destructive_change", c.address, f"{verb} (allowed in {env})"))
        else:
            out.append(_err("destructive_change", c.address, f"{verb} of a protected resource in {env}"))
    return out


def check_glue_jobs(plan: Plan, env: str) -> list[Finding]:
    out = []
    cap = ENV_LIMITS[env]["max_glue_workers"]
    for j in plan.by_type("aws_glue_job"):
        a = j.after
        workers = a.get("number_of_workers")
        if workers is not None and workers > cap:
            out.append(_err("glue_job_size", j.address, f"{workers} workers > {cap} allowed in {env}"))
        try:
            version = float(a.get("glue_version") or 0)
        except ValueError:
            version = 0.0
        if version < MIN_GLUE_VERSION:
            out.append(_err("glue_version", j.address,
                            f"glue_version {a.get('glue_version')!r}, minimum is {MIN_GLUE_VERSION}"))
        timeout = a.get("timeout")
        if timeout is None or timeout >= 2880:
            out.append(_warn("glue_timeout", j.address, "timeout left at Glue's 48h default"))
    return out


def check_athena_workgroups(plan: Plan, env: str) -> list[Finding]:
    out = []
    for w in plan.by_type("aws_athena_workgroup"):
        cfg = (w.after.get("configuration") or [{}])[0]
        if not cfg.get("enforce_workgroup_configuration"):
            out.append(_err("athena_workgroup", w.address, "workgroup settings not enforced; users can bypass the result location"))
        if not cfg.get("bytes_scanned_cutoff_per_query"):
            out.append(_warn("athena_workgroup", w.address, "no bytes scanned cutoff per query"))
    return out


ALL_RULES = (
    check_bucket_guardrails,
    check_required_tags,
    check_iam_wildcards,
    check_glue_jobs,
    check_athena_workgroups,
)


def run_all(plan: Plan, env: str, allow_destroy: set[str] | None = None) -> list[Finding]:
    if env not in ENV_LIMITS:
        raise ValueError(f"unknown environment {env!r}; expected one of {sorted(ENV_LIMITS)}")
    findings: list[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(plan, env))
    findings.extend(check_destructive(plan, env, allow_destroy))
    return sorted(findings, key=lambda f: (f.severity != "error", f.rule, f.address))


def summarize_actions(plan: Plan) -> dict[str, int]:
    counts = {"create": 0, "update": 0, "replace": 0, "delete": 0}
    for c in plan.changes:
        if c.actions == ["create"]:
            counts["create"] += 1
        elif c.actions == ["update"]:
            counts["update"] += 1
        elif c.actions == ["delete"]:
            counts["delete"] += 1
        elif "delete" in c.actions and "create" in c.actions:
            counts["replace"] += 1
    return counts
