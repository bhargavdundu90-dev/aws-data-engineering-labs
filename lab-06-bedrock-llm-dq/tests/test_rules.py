import csv
from datetime import date
from pathlib import Path

from rules import llm_view, mask_pii, rule_issues

TODAY = date(2026, 11, 3)
SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "kyc_applications.csv"


def records():
    with open(SAMPLE, newline="", encoding="utf-8") as fh:
        return {r["application_id"]: r for r in csv.DictReader(fh)}


def fields(issues):
    return {(i["field"], i["severity"]) for i in issues}


def test_clean_record_has_no_rule_issues():
    assert rule_issues(records()["APP-1001"], TODAY) == []


def test_minor_is_rejected():
    issues = rule_issues(records()["APP-1003"], TODAY)
    assert ("dob", "high") in fields(issues)
    assert "minor (age 15)" in issues[0]["issue"]


def test_pan_and_mobile_formats():
    assert ("pan", "high") in fields(rule_issues(records()["APP-1009"], TODAY))
    assert ("mobile", "medium") in fields(rule_issues(records()["APP-1007"], TODAY))


def test_pii_in_free_text_is_flagged():
    assert ("branch_remarks", "high") in fields(rule_issues(records()["APP-1006"], TODAY))
    # an "address" that is just a phone number
    assert ("address", "high") in fields(rule_issues(records()["APP-1005"], TODAY))


def test_missing_required_field():
    rec = dict(records()["APP-1001"], occupation="  ")
    assert ("occupation", "high") in fields(rule_issues(rec, TODAY))


def test_mask_pii_covers_pan_aadhaar_mobile_email():
    text = "PAN ABCPK1234L, aadhaar 4521 8877 1234, call +91 9876543210 or a.b@bank.in"
    assert mask_pii(text) == "PAN <PAN>, aadhaar <AADHAAR>, call <MOBILE> or <EMAIL>"


def test_mask_leaves_pin_codes_and_amounts_alone():
    assert mask_pii("Madhapur, Hyderabad 500081, income 1450000") == "Madhapur, Hyderabad 500081, income 1450000"


def test_llm_view_never_contains_structured_pii():
    for rec in records().values():
        view = llm_view(rec)
        assert set(view) == {"id", "occupation", "employer", "annual_income", "address", "branch_remarks"}
        blob = str(view)
        assert rec["pan"] not in blob and rec["customer_name"] not in blob
        if len(rec["mobile"]) == 10:
            assert rec["mobile"] not in blob
