import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "sample_plans"

TAGS = {
    "owner": "data-platform", "cost_center": "CC-1042", "data_classification": "restricted",
    "environment": "prod", "domain": "lending", "managed_by": "terraform",
}


@pytest.fixture
def good_doc():
    return json.loads((SAMPLES / "prod_good_plan.json").read_text())


@pytest.fixture
def risky_doc():
    return json.loads((SAMPLES / "prod_risky_plan.json").read_text())


def rc(address, rtype, actions=("create",), after=None, before=None):
    """One resource_changes entry in the shape `terraform show -json` writes."""
    parts = address.split(".")
    entry = {
        "address": address, "mode": "managed", "type": rtype, "name": parts[-1],
        "change": {"actions": list(actions), "before": before, "after": after, "after_unknown": {}},
    }
    if parts[0] == "module":
        entry["module_address"] = ".".join(parts[:2])
    return entry


def doc(*changes):
    return {"format_version": "1.2", "resource_changes": [copy.deepcopy(c) for c in changes]}


@pytest.fixture
def tags():
    return dict(TAGS)
