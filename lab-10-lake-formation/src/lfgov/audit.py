"""Audit what is actually granted in the account against the policy.

Input is the output of `aws lakeformation list-permissions` (PrincipalResourcePermissions).
Grants drift: someone fixes an urgent ticket with a direct table grant from the console
and it stays forever. This finds those grants and the settings that switch Lake Formation
off without anyone noticing.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from .policy import Policy, load

IAM_ALLOWED = "IAM_ALLOWED_PRINCIPALS"
PII = ("sensitivity", "pii")


@dataclass
class Finding:
    check: str
    severity: str     # error | warn
    principal: str
    resource: str
    detail: str


def _resource_name(res: dict) -> tuple[str, str | None, str | None, list[str] | None]:
    """(kind, database, table, explicit columns or None for all)."""
    if "Database" in res:
        return "database", res["Database"]["Name"], None, None
    if "Table" in res:
        t = res["Table"]
        return "table", t["DatabaseName"], t.get("Name"), None
    if "TableWithColumns" in res:
        t = res["TableWithColumns"]
        if "ColumnNames" in t:
            return "columns", t["DatabaseName"], t["Name"], list(t["ColumnNames"])
        excluded = set((t.get("ColumnWildcard") or {}).get("ExcludedColumnNames") or [])
        return "columns", t["DatabaseName"], t["Name"], ["*"] + [f"-{c}" for c in sorted(excluded)]
    if "DataCellsFilter" in res:
        f = res["DataCellsFilter"]
        return "filter", f["DatabaseName"], f["TableName"], None
    if "LFTagPolicy" in res:
        return "tag_policy", None, None, None
    return "other", None, None, None


def pii_principals(policy: Policy) -> set[str]:
    """ARNs whose tag grants include sensitivity=pii (the people allowed to read PII)."""
    out = set()
    for g in policy.tag_grants:
        if PII[1] in g.expression.get(PII[0], []):
            out.add(policy.principal_arn(g.principal))
    return out


def _pii_columns(policy: Policy, database: str, table: str) -> list[str]:
    try:
        tags = policy.column_tags(database, table)
    except Exception:
        return []
    return [c for c, t in tags.items() if t.get(PII[0]) == PII[1]]


def _visible_pii(policy, kind, database, table, columns) -> list[str]:
    pii = _pii_columns(policy, database, table) if table else []
    if not pii:
        return []
    if kind == "table" or columns is None:
        return pii
    if columns and columns[0] == "*":
        excluded = {c[1:] for c in columns[1:]}
        return [c for c in pii if c not in excluded]
    return [c for c in pii if c in columns]


def audit(policy: Policy, permissions: list[dict]) -> list[Finding]:
    admins = {policy.principal_arn(a) for a in policy.admins}
    allowed_pii = pii_principals(policy) | admins
    findings: list[Finding] = []
    for entry in permissions:
        principal = entry["Principal"]["DataLakePrincipalIdentifier"]
        res = entry["Resource"]
        perms = set(entry.get("Permissions") or [])
        grantable = set(entry.get("PermissionsWithGrantOption") or [])
        kind, database, table, columns = _resource_name(res)
        where = ".".join(x for x in (database, table) if x) or kind

        if principal == IAM_ALLOWED and perms:
            findings.append(Finding("iam_allowed_principals", "error", principal, where,
                                    "IAM-only access is still on; Lake Formation grants are not enforced here"))
            continue
        if principal in admins:
            continue
        if perms & {"ALL", "DROP"}:
            findings.append(Finding("broad_permission", "error", principal, where,
                                    f"non-admin holds {sorted(perms & {'ALL', 'DROP'})}"))
        if grantable:
            findings.append(Finding("grant_option", "warn", principal, where,
                                    f"can re-grant {sorted(grantable)}"))
        if ":user/" in principal:
            findings.append(Finding("iam_user_principal", "warn", principal, where,
                                    "grant to an IAM user; grant to a role so access follows the job, not the person"))
        if "SELECT" in perms and kind in ("table", "columns") and principal not in allowed_pii:
            leaked = _visible_pii(policy, kind, database, table, columns)
            if leaked:
                findings.append(Finding("pii_direct_grant", "error", principal, where,
                                        f"direct grant exposes PII columns {leaked}"))
        if kind in ("table", "columns"):
            findings.append(Finding("direct_grant_drift", "warn", principal, where,
                                    "named-resource grant outside the tag policy"))
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit Lake Formation grants against the policy")
    ap.add_argument("--policy", default="policy/lake_policy.yml")
    ap.add_argument("--permissions", required=True, help="JSON from `aws lakeformation list-permissions`")
    args = ap.parse_args(argv)
    policy = load(args.policy)
    with open(args.permissions) as fh:
        data = json.load(fh)
    findings = audit(policy, data.get("PrincipalResourcePermissions", data))
    for f in findings:
        print(f"{f.severity.upper():5} {f.check:24} {f.resource:32} {f.principal}  {f.detail}")
    errors = sum(f.severity == "error" for f in findings)
    print(f"{len(findings)} findings, {errors} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
