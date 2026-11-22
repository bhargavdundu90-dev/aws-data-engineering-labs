"""Tiny local evaluator for a subset of AWS Glue Data Quality (DQDL) rules.

Supported: RowCount, IsComplete, IsUnique, ColumnValues with >=, <=, > , <, between, in.
Lets rulesets be unit tested without a Glue job.
"""
from __future__ import annotations

import re

_NUM = r"-?\d+(?:\.\d+)?"


def parse_rule(text: str) -> dict:
    t = text.strip()
    m = re.fullmatch(r"RowCount\s*(>=|<=|>|<|=)\s*(\d+)", t)
    if m:
        return {"type": "RowCount", "op": m[1], "value": int(m[2])}
    m = re.fullmatch(r'IsComplete\s+"(\w+)"', t)
    if m:
        return {"type": "IsComplete", "column": m[1]}
    m = re.fullmatch(r'IsUnique\s+"(\w+)"', t)
    if m:
        return {"type": "IsUnique", "column": m[1]}
    m = re.fullmatch(rf'ColumnValues\s+"(\w+)"\s+between\s+({_NUM})\s+and\s+({_NUM})', t)
    if m:
        return {"type": "Between", "column": m[1], "lo": float(m[2]), "hi": float(m[3])}
    m = re.fullmatch(r'ColumnValues\s+"(\w+)"\s+in\s+\[(.+)\]', t)
    if m:
        vals = [v.strip().strip('"') for v in m[2].split(",")]
        return {"type": "In", "column": m[1], "values": vals}
    raise ValueError(f"unsupported rule: {text}")


def _cmp(a, op, b) -> bool:
    return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "=": a == b}[op]


def evaluate(rule_text: str, rows: list[dict]) -> bool:
    r = parse_rule(rule_text)
    k = r["type"]
    if k == "RowCount":
        return _cmp(len(rows), r["op"], r["value"])
    col = r.get("column")
    vals = [row.get(col) for row in rows]
    if k == "IsComplete":
        return all(v not in (None, "") for v in vals)
    if k == "IsUnique":
        return len(set(vals)) == len(vals)
    if k == "Between":
        return all(v is not None and r["lo"] <= float(v) <= r["hi"] for v in vals)
    return all(str(v) in r["values"] for v in vals)


def evaluate_ruleset(rules: list[str], rows: list[dict]) -> dict[str, bool]:
    return {rule: evaluate(rule, rows) for rule in rules}
