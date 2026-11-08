import json

import pytest

from conftest import doc, rc
from tfguard.plan import parse_plan
from tfguard.rules import (check_athena_workgroups, check_bucket_guardrails, check_destructive,
                           check_glue_jobs, check_iam_wildcards, check_required_tags, run_all,
                           summarize_actions)


def rules_hit(findings, severity="error"):
    return {(f.rule, f.address) for f in findings if f.severity == severity}


def test_good_plan_is_clean(good_doc):
    assert run_all(parse_plan(good_doc), "prod") == []


def test_risky_plan_errors(risky_doc):
    errors = rules_hit(run_all(parse_plan(risky_doc), "prod"))
    assert ("bucket_encryption", "module.curated.aws_s3_bucket.this") in errors
    assert ("bucket_public_access", "aws_s3_bucket.adhoc_exports") in errors
    assert ("bucket_force_destroy", "module.raw.aws_s3_bucket.this") in errors
    assert ("iam_wildcard", "module.curate_job.aws_iam_role_policy.job") in errors
    assert ("destructive_change", "aws_kms_key.lake") in errors
    assert ("glue_version", "module.curate_job.aws_glue_job.this") in errors
    assert ("required_tags", "module.curated.aws_s3_bucket.this") in errors


def test_errors_sorted_before_warnings(risky_doc):
    sev = [f.severity for f in run_all(parse_plan(risky_doc), "prod")]
    assert sev == sorted(sev, key=lambda s: s != "error")


# ---- buckets ---------------------------------------------------------------

def _bucket(mod="module.raw", force=False, tags=None):
    return rc(f"{mod}.aws_s3_bucket.this", "aws_s3_bucket",
              after={"bucket": "b", "force_destroy": force, "tags_all": tags or {}})


def _pab(mod="module.raw", **flags):
    after = {k: True for k in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")}
    after.update(flags)
    return rc(f"{mod}.aws_s3_bucket_public_access_block.this", "aws_s3_bucket_public_access_block", after=after)


def _sse(mod="module.raw", algo="aws:kms"):
    return rc(f"{mod}.aws_s3_bucket_server_side_encryption_configuration.this",
              "aws_s3_bucket_server_side_encryption_configuration",
              after={"rule": [{"apply_server_side_encryption_by_default": [{"sse_algorithm": algo}]}]})


def _ver(mod="module.raw"):
    return rc(f"{mod}.aws_s3_bucket_versioning.this", "aws_s3_bucket_versioning", after={})


def test_public_access_flag_turned_off_is_named():
    f = check_bucket_guardrails(parse_plan(doc(_bucket(), _pab(restrict_public_buckets=False), _sse(), _ver())), "prod")
    assert len(f) == 1 and "restrict_public_buckets" in f[0].message


def test_companion_resources_matched_per_module_not_globally():
    # raw has everything; curated relies on raw's resources -> must still fail
    plan = parse_plan(doc(_bucket(), _pab(), _sse(), _ver(), _bucket("module.curated")))
    hits = rules_hit(check_bucket_guardrails(plan, "prod"))
    assert ("bucket_public_access", "module.curated.aws_s3_bucket.this") in hits
    assert ("bucket_encryption", "module.curated.aws_s3_bucket.this") in hits
    assert not any(a.startswith("module.raw") for _, a in hits)


def test_missing_versioning_is_only_a_warning():
    f = check_bucket_guardrails(parse_plan(doc(_bucket(), _pab(), _sse())), "prod")
    assert [(x.rule, x.severity) for x in f] == [("bucket_versioning", "warn")]


def test_force_destroy_allowed_in_dev():
    plan = parse_plan(doc(_bucket(force=True), _pab(), _sse(), _ver()))
    assert check_bucket_guardrails(plan, "dev") == []
    assert rules_hit(check_bucket_guardrails(plan, "prod")) == {("bucket_force_destroy", "module.raw.aws_s3_bucket.this")}


# ---- tags ------------------------------------------------------------------

def test_tags_all_from_default_tags_satisfies_rule(tags):
    plan = parse_plan(doc(rc("aws_kms_key.k", "aws_kms_key", after={"tags": {}, "tags_all": tags})))
    assert check_required_tags(plan, "prod") == []


def test_wrong_environment_tag_caught(tags):
    plan = parse_plan(doc(rc("aws_kms_key.k", "aws_kms_key", after={"tags_all": tags})))
    f = check_required_tags(plan, "dev")
    assert len(f) == 1 and "'prod'" in f[0].message


def test_empty_tag_value_counts_as_missing(tags):
    tags["owner"] = ""
    plan = parse_plan(doc(rc("aws_glue_job.j", "aws_glue_job", after={"tags_all": tags})))
    assert "owner" in check_required_tags(plan, "prod")[0].message


def test_untaggable_and_noop_resources_ignored():
    plan = parse_plan(doc(
        rc("aws_s3_bucket_versioning.v", "aws_s3_bucket_versioning", after={}),
        rc("aws_kms_key.k", "aws_kms_key", actions=("no-op",), after={}),
    ))
    assert check_required_tags(plan, "prod") == []


# ---- IAM -------------------------------------------------------------------

def _policy(*statements):
    return rc("aws_iam_role_policy.p", "aws_iam_role_policy",
              after={"policy": json.dumps({"Version": "2012-10-17", "Statement": list(statements)})})


@pytest.mark.parametrize("action", ["*", "s3:*", ["s3:GetObject", "glue:*"]])
def test_wildcard_action_on_star_resource_is_error(action):
    f = check_iam_wildcards(parse_plan(doc(_policy({"Effect": "Allow", "Action": action, "Resource": "*"}))), "prod")
    assert [x.severity for x in f] == ["error"]


def test_scoped_wildcards_are_warnings():
    plan = parse_plan(doc(_policy(
        {"Sid": "A", "Effect": "Allow", "Action": "s3:*", "Resource": "arn:aws:s3:::b/*"},
        {"Sid": "B", "Effect": "Allow", "Action": "logs:PutLogEvents", "Resource": "*"},
    )))
    assert [x.severity for x in check_iam_wildcards(plan, "prod")] == ["warn", "warn"]


def test_deny_statements_and_single_statement_dict():
    stmt = {"Effect": "Deny", "Action": "s3:*", "Resource": "*"}
    plan = parse_plan(doc(rc("aws_s3_bucket_policy.p", "aws_s3_bucket_policy",
                             after={"policy": json.dumps({"Statement": stmt})})))
    assert check_iam_wildcards(plan, "prod") == []


def test_unknown_policy_is_skipped():
    plan = parse_plan(doc(rc("aws_iam_role_policy.p", "aws_iam_role_policy", after={})))
    assert check_iam_wildcards(plan, "prod") == []


# ---- destructive -----------------------------------------------------------

def test_destroy_blocked_in_prod_warned_in_dev():
    plan = parse_plan(doc(rc("module.raw.aws_s3_bucket.this", "aws_s3_bucket", actions=("delete",), before={})))
    assert check_destructive(plan, "prod")[0].severity == "error"
    assert check_destructive(plan, "dev")[0].severity == "warn"


def test_allow_destroy_by_address():
    addr = "module.raw_db.aws_glue_catalog_database.this"
    plan = parse_plan(doc(rc(addr, "aws_glue_catalog_database", actions=("delete",), before={})))
    f = check_destructive(plan, "prod", {addr})
    assert f[0].severity == "warn" and "allowed" in f[0].message


def test_replace_of_unprotected_type_is_fine():
    plan = parse_plan(doc(rc("aws_glue_job.j", "aws_glue_job", actions=("delete", "create"), after={})))
    assert check_destructive(plan, "prod") == []


# ---- glue / athena ---------------------------------------------------------

def _job(**after):
    base = {"glue_version": "4.0", "number_of_workers": 2, "timeout": 60}
    base.update(after)
    return rc("module.j.aws_glue_job.this", "aws_glue_job", after=base)


def test_worker_cap_differs_per_env():
    plan = parse_plan(doc(_job(number_of_workers=10)))
    assert rules_hit(check_glue_jobs(plan, "dev")) == {("glue_job_size", "module.j.aws_glue_job.this")}
    assert check_glue_jobs(plan, "prod") == []


def test_glue_version_and_timeout():
    f = check_glue_jobs(parse_plan(doc(_job(glue_version="2.0", timeout=None))), "prod")
    assert {(x.rule, x.severity) for x in f} == {("glue_version", "error"), ("glue_timeout", "warn")}


def test_athena_workgroup_needs_enforcement_and_cutoff():
    wg = rc("aws_athena_workgroup.w", "aws_athena_workgroup", after={"configuration": [{}]})
    f = check_athena_workgroups(parse_plan(doc(wg)), "prod")
    assert {(x.rule, x.severity) for x in f} == {("athena_workgroup", "error"), ("athena_workgroup", "warn")}


def test_summarize_actions(risky_doc):
    assert summarize_actions(parse_plan(risky_doc)) == {"create": 10, "update": 1, "replace": 1, "delete": 1}


def test_unknown_env_rejected(good_doc):
    with pytest.raises(ValueError):
        run_all(parse_plan(good_doc), "staging")
