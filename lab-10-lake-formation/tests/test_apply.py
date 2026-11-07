import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from lfgov.apply import admin_settings, apply_plan, build_plan

ENG = "arn:aws:iam::111122223333:role/lending-data-engineer"


def test_plan_order_tags_before_grants(policy):
    ops = [c.operation for c in build_plan(policy)]
    last_tag = max(i for i, o in enumerate(ops) if o in ("create_lf_tag", "add_lf_tags_to_resource"))
    first_grant = ops.index("grant_permissions")
    assert last_tag < first_grant
    for i, o in enumerate(ops):                       # each filter is created before it is granted
        if o == "grant_permissions" and "DataCellsFilter" in build_plan(policy)[i].params["Resource"]:
            assert "create_data_cells_filter" in ops[:i]


def test_columns_with_same_tags_go_in_one_call(policy):
    calls = [c for c in build_plan(policy) if c.operation == "add_lf_tags_to_resource"
             and "TableWithColumns" in c.params["Resource"]]
    cust = [c for c in calls if c.params["Resource"]["TableWithColumns"]["Name"] == "customers"]
    pii = [c for c in cust if c.params["LFTags"][0]["TagValues"] == ["pii"]]
    assert len(pii) == 1
    assert pii[0].params["Resource"]["TableWithColumns"]["ColumnNames"] == ["full_name", "pan_number", "mobile"]


def test_filter_uses_column_wildcard_with_exclusions(policy):
    (f,) = [c for c in build_plan(policy) if c.operation == "create_data_cells_filter"
            and c.params["TableData"]["Name"] == "customers_south_no_pii"]
    td = f.params["TableData"]
    assert td["RowFilter"] == {"FilterExpression": "region = 'SOUTH'"}
    assert td["ColumnWildcard"] == {"ExcludedColumnNames": ["full_name", "pan_number", "mobile"]}


def test_admin_settings_keep_existing_admins_and_drop_iam_defaults(policy):
    current = {"DataLakeAdmins": [{"DataLakePrincipalIdentifier": "arn:aws:iam::111122223333:role/platform"}],
               "CreateDatabaseDefaultPermissions": [{"Principal": {"DataLakePrincipalIdentifier":
                                                                   "IAM_ALLOWED_PRINCIPALS"},
                                                     "Permissions": ["ALL"]}]}
    out = admin_settings(policy, current)
    ids = [a["DataLakePrincipalIdentifier"] for a in out["DataLakeAdmins"]]
    assert ids == ["arn:aws:iam::111122223333:role/platform", ENG]
    assert out["CreateDatabaseDefaultPermissions"] == [] and out["CreateTableDefaultPermissions"] == []
    assert admin_settings(policy, out)["DataLakeAdmins"] == out["DataLakeAdmins"]   # idempotent


def _client():
    return boto3.client("lakeformation", region_name="ap-south-1",
                        aws_access_key_id="x", aws_secret_access_key="x")


def test_apply_runs_every_call_in_order_and_treats_existing_as_done(policy):
    client = _client()
    calls = build_plan(policy)
    with Stubber(client) as stub:
        stub.add_response("get_data_lake_settings", {"DataLakeSettings": {"DataLakeAdmins": []}},
                          {"CatalogId": policy.account_id})
        stub.add_response("put_data_lake_settings", {},
                          {"CatalogId": policy.account_id,
                           "DataLakeSettings": admin_settings(policy, {"DataLakeAdmins": []})})
        for i, call in enumerate(calls):
            if i == 0:   # the first tag already exists from an earlier run
                stub.add_client_error(call.operation, "AlreadyExistsException", expected_params=call.params)
            else:
                stub.add_response(call.operation, {}, call.params)
        results = apply_plan(client, policy, calls)
        stub.assert_no_pending_responses()
    assert results[1] == {"operation": "create_lf_tag", "status": "exists"}
    assert all(r["status"] in ("ok", "exists") for r in results)
    assert len(results) == len(calls) + 1


def test_apply_stops_on_a_real_error(policy):
    client = _client()
    calls = build_plan(policy)
    with Stubber(client) as stub:
        stub.add_response("get_data_lake_settings", {"DataLakeSettings": {}})
        stub.add_response("put_data_lake_settings", {})
        stub.add_client_error("create_lf_tag", "AccessDeniedException")
        with pytest.raises(ClientError):
            apply_plan(client, policy, calls)
