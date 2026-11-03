"""Deterministic checks and PII masking, run before anything is sent to the LLM.

Rules are free, fast and explainable, so they handle everything a regex or a comparison
can decide. The LLM only sees what rules cannot judge (does this occupation make sense
with this employer and income, is this address an address at all), and it only sees it
after PAN, Aadhaar and mobile numbers are masked.
"""
from __future__ import annotations

import re
from datetime import date

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
MOBILE_RE = re.compile(r"^[6-9][0-9]{9}$")

# patterns to mask inside free text before it leaves our account
_MASKS = [
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "<PAN>"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "<AADHAAR>"),
    (re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"), "<MOBILE>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<EMAIL>"),
]

REQUIRED = ["application_id", "customer_name", "dob", "pan", "mobile", "occupation", "address"]
# free-text columns the LLM reviews; structured columns are covered by rules
FREE_TEXT = ["occupation", "employer", "annual_income", "address", "branch_remarks"]


def mask_pii(text: str) -> str:
    out = text or ""
    for pattern, token in _MASKS:
        out = pattern.sub(token, out)
    return out


def age_on(dob: str, today: date) -> int | None:
    try:
        d = date.fromisoformat(dob)
    except (TypeError, ValueError):
        return None
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def rule_issues(rec: dict, today: date) -> list[dict]:
    """Return a list of {"field", "issue", "severity"} found by deterministic rules."""
    issues = []
    for col in REQUIRED:
        if not (rec.get(col) or "").strip():
            issues.append({"field": col, "issue": "missing", "severity": "high"})

    if rec.get("pan") and not PAN_RE.match(rec["pan"].strip().upper()):
        issues.append({"field": "pan", "issue": "PAN format invalid", "severity": "high"})
    if rec.get("mobile") and not MOBILE_RE.match(rec["mobile"].strip()):
        issues.append({"field": "mobile", "issue": "mobile is not a 10-digit Indian number", "severity": "medium"})

    age = age_on(rec.get("dob", ""), today)
    if rec.get("dob") and age is None:
        issues.append({"field": "dob", "issue": "dob is not a date", "severity": "high"})
    elif age is not None and age < 18:
        issues.append({"field": "dob", "issue": f"applicant is a minor (age {age})", "severity": "high"})

    income = (rec.get("annual_income") or "").strip()
    if income and not income.isdigit():
        issues.append({"field": "annual_income", "issue": "income is not a whole number", "severity": "medium"})

    for col in FREE_TEXT:
        raw = rec.get(col) or ""
        if raw and mask_pii(raw) != raw:
            issues.append({"field": col, "issue": "PII written in a free-text field", "severity": "high"})
    return issues


def llm_view(rec: dict) -> dict:
    """The masked subset of a record the LLM is allowed to see."""
    view = {"id": rec["application_id"]}
    view.update({col: mask_pii(rec.get(col, "")) for col in FREE_TEXT})
    return view
