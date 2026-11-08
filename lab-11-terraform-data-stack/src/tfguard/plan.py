"""Read a Terraform plan JSON (`terraform show -json tfplan`) into simple resource records."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Change:
    address: str            # module.raw.aws_s3_bucket.this
    module: str             # module.raw ("" for the root module)
    type: str               # aws_s3_bucket
    name: str               # this
    actions: list[str]      # ["create"], ["update"], ["delete", "create"], ["no-op"] ...
    after: dict = field(default_factory=dict)
    before: dict = field(default_factory=dict)
    after_unknown: dict = field(default_factory=dict)

    @property
    def is_delete(self) -> bool:
        """True for a destroy or a replace (delete+create in either order)."""
        return "delete" in self.actions

    @property
    def is_delete_only(self) -> bool:
        return self.actions == ["delete"]

    @property
    def is_noop(self) -> bool:
        return self.actions in (["no-op"], ["read"])


@dataclass
class Plan:
    changes: list[Change]
    terraform_version: str = ""

    def by_type(self, rtype: str) -> list[Change]:
        return [c for c in self.changes if c.type == rtype and not c.is_delete_only]

    def in_module(self, module: str, rtype: str) -> list[Change]:
        return [c for c in self.by_type(rtype) if c.module == module]


def parse_plan(doc: dict) -> Plan:
    if "resource_changes" not in doc:
        raise ValueError("not a plan JSON: no resource_changes (use `terraform show -json tfplan`)")
    changes = []
    for rc in doc["resource_changes"]:
        if rc.get("mode", "managed") != "managed":
            continue  # data sources are reads, not changes
        ch = rc.get("change", {})
        changes.append(Change(
            address=rc["address"],
            module=rc.get("module_address", ""),
            type=rc["type"],
            name=rc["name"],
            actions=list(ch.get("actions", [])),
            after=ch.get("after") or {},
            before=ch.get("before") or {},
            after_unknown=ch.get("after_unknown") or {},
        ))
    return Plan(changes=changes, terraform_version=doc.get("terraform_version", ""))


def load_plan(path: str | Path) -> Plan:
    with open(path, encoding="utf-8") as fh:
        return parse_plan(json.load(fh))
