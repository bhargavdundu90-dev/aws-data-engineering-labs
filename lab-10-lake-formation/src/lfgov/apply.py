"""Turn the policy into Lake Formation API calls and apply them.

`build_plan` is pure (policy in, list of calls out) so it can be reviewed in a
pull request with `--dry-run`. `apply_plan` runs the calls in order with a boto3
client and treats "already exists" as done, so re-running is safe.

Order matters: tags must exist before they are attached, and resources must carry
their tags before a tag-based grant can resolve to anything.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass

from botocore.exceptions import ClientError

from .policy import Policy, load


@dataclass
class Call:
    operation: str
    params: dict

    def to_dict(self) -> dict:
        return {"operation": self.operation, "params": self.params}


def _principal(policy: Policy, name: str) -> dict:
    return {"DataLakePrincipalIdentifier": policy.principal_arn(name)}


def build_plan(policy: Policy) -> list[Call]:
    cat = policy.account_id
    calls: list[Call] = []

    for key, values in policy.lf_tags.items():
        calls.append(Call("create_lf_tag", {"CatalogId": cat, "TagKey": key, "TagValues": list(values)}))

    for db in policy.databases.values():
        if db.tags:
            calls.append(Call("add_lf_tags_to_resource", {
                "CatalogId": cat,
                "Resource": {"Database": {"CatalogId": cat, "Name": db.name}},
                "LFTags": [{"CatalogId": cat, "TagKey": k, "TagValues": [v]} for k, v in db.tags.items()],
            }))
        for t in db.tables.values():
            # columns with the same override are tagged in one call
            groups: dict[tuple, list[str]] = defaultdict(list)
            for col in t.columns:
                override = t.column_tags.get(col)
                if override:
                    groups[tuple(sorted(override.items()))].append(col)
            for tag_items, cols in groups.items():
                calls.append(Call("add_lf_tags_to_resource", {
                    "CatalogId": cat,
                    "Resource": {"TableWithColumns": {"CatalogId": cat, "DatabaseName": db.name,
                                                      "Name": t.name, "ColumnNames": cols}},
                    "LFTags": [{"CatalogId": cat, "TagKey": k, "TagValues": [v]} for k, v in tag_items],
                }))

    for g in policy.tag_grants:
        expression = [{"TagKey": k, "TagValues": list(v)} for k, v in g.expression.items()]
        # DESCRIBE on the matching databases so the tables show up in Athena's catalog browser
        db_expression = [e for e in expression if e["TagKey"] != "sensitivity"] or expression
        calls.append(Call("grant_permissions", {
            "CatalogId": cat, "Principal": _principal(policy, g.principal),
            "Resource": {"LFTagPolicy": {"CatalogId": cat, "ResourceType": "DATABASE",
                                         "Expression": db_expression}},
            "Permissions": ["DESCRIBE"],
        }))
        calls.append(Call("grant_permissions", {
            "CatalogId": cat, "Principal": _principal(policy, g.principal),
            "Resource": {"LFTagPolicy": {"CatalogId": cat, "ResourceType": "TABLE",
                                         "Expression": expression}},
            "Permissions": list(g.permissions),
        }))

    for f in policy.data_filters:
        table_data = {"TableCatalogId": cat, "DatabaseName": f.database, "TableName": f.table, "Name": f.name,
                      "RowFilter": ({"FilterExpression": f.row_filter} if f.row_filter
                                    else {"AllRowsWildcard": {}})}
        if f.columns:
            table_data["ColumnNames"] = list(f.columns)
        else:
            table_data["ColumnWildcard"] = {"ExcludedColumnNames": list(f.excluded_columns or [])}
        calls.append(Call("create_data_cells_filter", {"TableData": table_data}))
        for p in f.grant_to:
            calls.append(Call("grant_permissions", {
                "CatalogId": cat, "Principal": _principal(policy, p),
                "Resource": {"DataCellsFilter": {"TableCatalogId": cat, "DatabaseName": f.database,
                                                 "TableName": f.table, "Name": f.name}},
                "Permissions": ["SELECT"],
            }))
    return calls


def admin_settings(policy: Policy, current: dict) -> dict:
    """Merge our admins into the current settings and switch off the IAM-only defaults.

    PutDataLakeSettings replaces the whole document, so start from GetDataLakeSettings
    instead of writing a fresh one (that would drop admins added outside this repo).
    """
    settings = dict(current)
    admins = list(settings.get("DataLakeAdmins") or [])
    known = {a["DataLakePrincipalIdentifier"] for a in admins}
    for name in policy.admins:
        arn = policy.principal_arn(name)
        if arn not in known:
            admins.append({"DataLakePrincipalIdentifier": arn})
    settings["DataLakeAdmins"] = admins
    # new databases/tables should not get the IAMAllowedPrincipals ALL grant
    settings["CreateDatabaseDefaultPermissions"] = []
    settings["CreateTableDefaultPermissions"] = []
    return settings


ALREADY_DONE = {"AlreadyExistsException"}


def apply_plan(client, policy: Policy, calls: list[Call]) -> list[dict]:
    results = []
    current = client.get_data_lake_settings(CatalogId=policy.account_id)["DataLakeSettings"]
    client.put_data_lake_settings(CatalogId=policy.account_id, DataLakeSettings=admin_settings(policy, current))
    results.append({"operation": "put_data_lake_settings", "status": "ok"})
    for call in calls:
        try:
            getattr(client, call.operation)(**call.params)
            results.append({"operation": call.operation, "status": "ok"})
        except ClientError as err:
            code = err.response["Error"]["Code"]
            if code in ALREADY_DONE:
                results.append({"operation": call.operation, "status": "exists"})
                continue
            results.append({"operation": call.operation, "status": "failed", "error": code})
            raise
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply the Lake Formation policy")
    ap.add_argument("--policy", default="policy/lake_policy.yml")
    ap.add_argument("--dry-run", action="store_true", help="print the API calls and exit")
    ap.add_argument("--region", default="ap-south-1")
    args = ap.parse_args(argv)

    policy = load(args.policy)
    calls = build_plan(policy)
    if args.dry_run:
        print(json.dumps([c.to_dict() for c in calls], indent=2))
        return 0
    import boto3
    client = boto3.client("lakeformation", region_name=args.region)
    for r in apply_plan(client, policy, calls):
        print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
