import json

from lfgov.audit import audit, main, pii_principals

ANALYST = "arn:aws:iam::111122223333:role/risk-analyst"


def entry(principal, resource, perms, grantable=()):
    return {"Principal": {"DataLakePrincipalIdentifier": principal}, "Resource": resource,
            "Permissions": list(perms), "PermissionsWithGrantOption": list(grantable)}


def table(name):
    return {"Table": {"CatalogId": "111122223333", "DatabaseName": "lending_curated", "Name": name}}


def cols(name, columns=None, excluded=None):
    t = {"CatalogId": "111122223333", "DatabaseName": "lending_curated", "Name": name}
    if columns is not None:
        t["ColumnNames"] = columns
    else:
        t["ColumnWildcard"] = {"ExcludedColumnNames": excluded or []}
    return {"TableWithColumns": t}


def checks(findings):
    return sorted((f.check, f.severity) for f in findings)


def test_pii_principals_come_from_tag_grants(policy):
    assert pii_principals(policy) == {"arn:aws:iam::111122223333:role/lending-data-engineer",
                                      "arn:aws:iam::111122223333:role/compliance-auditor"}


def test_iam_allowed_principals_is_an_error(policy):
    f = audit(policy, [entry("IAM_ALLOWED_PRINCIPALS", table("customers"), ["ALL"])])
    assert checks(f) == [("iam_allowed_principals", "error")]


def test_table_grant_to_analyst_leaks_pii(policy):
    f = audit(policy, [entry(ANALYST, table("customers"), ["SELECT"])])
    assert ("pii_direct_grant", "error") in checks(f)


def test_column_grant_without_pii_is_only_drift(policy):
    f = audit(policy, [entry(ANALYST, cols("customers", ["customer_id", "risk_tier"]), ["SELECT"])])
    assert checks(f) == [("direct_grant_drift", "warn")]


def test_wildcard_excluding_all_pii_is_clean_of_pii(policy):
    excl = audit(policy, [entry(ANALYST, cols("customers", excluded=["full_name", "pan_number", "mobile"]),
                                ["SELECT"])])
    partial = audit(policy, [entry(ANALYST, cols("customers", excluded=["pan_number"]), ["SELECT"])])
    assert ("pii_direct_grant", "error") not in checks(excl)
    leak = [f for f in partial if f.check == "pii_direct_grant"][0]
    assert "full_name" in leak.detail and "pan_number" not in leak.detail


def test_grant_option_drop_and_iam_user(policy):
    f = audit(policy, [entry("arn:aws:iam::111122223333:user/temp", table("loan_accounts"),
                             ["SELECT", "DROP"], ["SELECT"])])
    assert checks(f) == [("broad_permission", "error"), ("direct_grant_drift", "warn"),
                         ("grant_option", "warn"), ("iam_user_principal", "warn")]


def test_admins_and_filters_are_not_flagged(policy):
    f = audit(policy, [
        entry("arn:aws:iam::111122223333:role/lending-data-engineer", table("customers"), ["ALL"], ["ALL"]),
        entry("arn:aws:iam::111122223333:role/branch-ops-south",
              {"DataCellsFilter": {"TableCatalogId": "111122223333", "DatabaseName": "lending_curated",
                                   "TableName": "customers", "Name": "customers_south_no_pii"}}, ["SELECT"]),
    ])
    assert f == []


def test_cli_exit_code_on_sample_snapshot(root, capsys):
    code = main(["--policy", str(root / "policy" / "lake_policy.yml"),
                 "--permissions", str(root / "sample_data" / "permissions_snapshot.json")])
    out = capsys.readouterr().out
    assert code == 1 and "4 errors" in out


def test_cli_clean_snapshot_passes(root, tmp_path):
    p = tmp_path / "perms.json"
    p.write_text(json.dumps({"PrincipalResourcePermissions": []}))
    assert main(["--policy", str(root / "policy" / "lake_policy.yml"), "--permissions", str(p)]) == 0
