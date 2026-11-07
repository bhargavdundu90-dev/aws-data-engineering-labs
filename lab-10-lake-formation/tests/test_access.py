import pytest

from lfgov.access import (AmbiguousAccess, FilterSyntaxError, access_paths, compile_filter,
                          preview, tags_match)

DB = "lending_curated"
PII = {"full_name", "pan_number", "mobile"}


def test_tag_expression_semantics():
    expr = {"domain": ["lending"], "sensitivity": ["public", "internal"]}
    assert tags_match({"domain": "lending", "sensitivity": "public"}, expr)
    assert not tags_match({"domain": "lending", "sensitivity": "pii"}, expr)
    assert not tags_match({"domain": "cards", "sensitivity": "public"}, expr)
    assert not tags_match({"domain": "lending"}, expr)      # missing key never matches


def test_risk_analyst_gets_no_pii_columns(policy):
    (path,) = access_paths(policy, "risk_analyst", DB, "customers")
    assert not PII & set(path.columns)
    assert path.columns == ["customer_id", "city", "branch_code", "region", "risk_tier", "kyc_status"]
    assert path.row_filter is None


def test_compliance_sees_everything(policy):
    (path,) = access_paths(policy, "compliance_auditor", DB, "customers")
    assert path.columns == policy.table(DB, "customers").columns


def test_branch_ops_only_through_filter(policy, root):
    out = preview(policy, "branch_ops_south", DB, "customers", root / "sample_data" / "customers.csv")
    assert out["access"] == "filter:customers_south_no_pii"
    assert {r["region"] for r in out["rows"]} == {"SOUTH"}
    assert len(out["rows"]) == 3
    assert not PII & set(out["columns"])


def test_unknown_principal_sees_nothing(policy, root):
    out = preview(policy, "nobody", DB, "customers", root / "sample_data" / "customers.csv")
    assert out["access"] == "none" and out["rows"] == []


def test_two_select_paths_are_not_guessed(policy, root):
    policy.data_filters[0].grant_to.append("risk_analyst")
    with pytest.raises(AmbiguousAccess):
        preview(policy, "risk_analyst", DB, "customers", root / "sample_data" / "customers.csv")


@pytest.mark.parametrize("expr, row, expected", [
    ("region = 'SOUTH'", {"region": "SOUTH"}, True),
    ("region = 'SOUTH'", {"region": "WEST"}, False),
    ("region IN ('SOUTH', 'WEST')", {"region": "WEST"}, True),
    ("region NOT IN ('SOUTH')", {"region": "WEST"}, True),
    ("dpd >= 90", {"dpd": "95"}, True),
    ("dpd >= 90", {"dpd": "9"}, False),          # numeric, not string comparison
    ("dpd > 0 AND region = 'SOUTH'", {"dpd": "1", "region": "SOUTH"}, True),
    ("dpd > 0 AND region = 'SOUTH'", {"dpd": "0", "region": "SOUTH"}, False),
    ("region = 'SOUTH'", {"region": ""}, False),  # null never passes
    ("branch_code = 'A AND B'", {"branch_code": "A AND B"}, True),
    ("TRUE", {}, True),
])
def test_row_filter(expr, row, expected):
    assert compile_filter(expr)(row) is expected


def test_unsupported_filter_is_an_error():
    with pytest.raises(FilterSyntaxError):
        compile_filter("region LIKE 'S%'")
