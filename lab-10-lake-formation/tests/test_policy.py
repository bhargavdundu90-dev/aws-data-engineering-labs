import copy

import pytest
import yaml

from lfgov.policy import PolicyError, parse


def raw(root):
    return yaml.safe_load((root / "policy" / "lake_policy.yml").read_text())


def test_policy_file_loads(policy):
    assert set(policy.databases) == {"lending_curated"}
    assert set(policy.databases["lending_curated"].tables) == {"customers", "loan_accounts"}
    assert len(policy.tag_grants) == 3 and len(policy.data_filters) == 2


def test_column_tags_inherit_and_override(policy):
    tags = policy.column_tags("lending_curated", "customers")
    assert tags["customer_id"] == {"domain": "lending", "sensitivity": "internal"}   # from the database
    assert tags["pan_number"] == {"domain": "lending", "sensitivity": "pii"}         # column override
    assert tags["city"]["sensitivity"] == "public"


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["databases"]["lending_curated"]["tables"]["customers"]["column_tags"]
        .update({"pan_number": {"sensitivity": "secret"}}), "not allowed"),
    (lambda r: r["databases"]["lending_curated"]["tables"]["customers"]["column_tags"]
        .update({"aadhaar": {"sensitivity": "pii"}}), "unknown column"),
    (lambda r: r["databases"]["lending_curated"].update({"tags": {"owner": "risk"}}), "not defined"),
    (lambda r: r["tag_grants"][0].update({"principal": "intern"}), "unknown principal"),
    (lambda r: r["tag_grants"][1].update({"permissions": ["SELECT", "READ"]}), "unknown permissions"),
    (lambda r: r["data_filters"][0].update({"columns": ["customer_id"]}), "not both"),
    (lambda r: r["data_filters"][1].update({"name": "customers_south_no_pii"}), "duplicate"),
    (lambda r: r["data_filters"][0].update({"table": "cards"}), "unknown table"),
    (lambda r: r.update({"admins": ["root"]}), "not a defined principal"),
])
def test_inconsistent_policy_is_rejected(root, mutate, message):
    r = copy.deepcopy(raw(root))
    mutate(r)
    with pytest.raises(PolicyError, match=message):
        parse(r)
