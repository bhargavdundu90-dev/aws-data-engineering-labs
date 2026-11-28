import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from access_diagnose import diagnose, _allows

IAM = [{"Effect": "Allow", "Action": ["s3:GetObject", "athena:*"]}]


def test_missing_lf_grant_is_first_failure():
    r = diagnose({"lf_grants": [], "iam_statements": IAM})
    assert r.first_failure.check == "lake_formation_grant"


def test_all_good():
    r = diagnose({"lf_grants": ["SELECT"], "iam_statements": IAM})
    assert r.first_failure is None


def test_explicit_deny_beats_allow():
    st = IAM + [{"Effect": "Deny", "Action": "s3:*"}]
    assert not _allows(st, "s3:GetObject")


def test_kms_denied():
    r = diagnose({"lf_grants": ["SELECT"], "iam_statements": IAM, "kms_statements": []})
    assert r.first_failure.check == "kms_key_policy"


def test_resource_link_needs_target_grant():
    r = diagnose({"lf_grants": ["SELECT"], "iam_statements": IAM,
                  "is_resource_link": True, "link_target_grants": []})
    assert r.first_failure.check == "resource_link_target"
