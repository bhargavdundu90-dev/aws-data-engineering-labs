import pytest

from conftest import doc, rc
from tfguard.plan import parse_plan


def test_module_and_actions_parsed(good_doc):
    plan = parse_plan(good_doc)
    job = next(c for c in plan.changes if c.type == "aws_glue_job")
    assert job.module == "module.curate_job"
    assert job.actions == ["create"]
    assert job.after["number_of_workers"] == 10


def test_data_sources_are_skipped(good_doc):
    plan = parse_plan(good_doc)
    assert all(not c.address.split(".")[2:3] == ["data"] for c in plan.changes)
    assert not any(c.type == "aws_caller_identity" for c in plan.changes)


def test_replace_counts_as_delete_but_not_delete_only():
    plan = parse_plan(doc(rc("aws_kms_key.lake", "aws_kms_key", actions=("create", "delete"), after={})))
    c = plan.changes[0]
    assert c.is_delete and not c.is_delete_only


def test_destroy_has_empty_after_and_is_excluded_from_by_type():
    plan = parse_plan(doc(rc("aws_s3_bucket.old", "aws_s3_bucket", actions=("delete",), before={"bucket": "x"})))
    assert plan.changes[0].after == {}
    assert plan.by_type("aws_s3_bucket") == []


def test_rejects_state_or_config_json():
    with pytest.raises(ValueError, match="resource_changes"):
        parse_plan({"values": {"root_module": {}}})
