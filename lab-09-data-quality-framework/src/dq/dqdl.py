"""Translate a suite into an AWS Glue Data Quality (DQDL) ruleset.

The YAML suite stays the source of truth and runs anywhere Spark runs (laptop, CI, EMR, Glue). When the
same table is also checked by Glue Data Quality in the catalog, the ruleset is generated from the suite so
the two never drift apart. Checks with no clean DQDL equivalent are returned separately, not dropped silently.
"""
from __future__ import annotations

import json

from dq.suite import Check, Suite


def _q(s: str) -> str:
    return json.dumps(s)  # DQDL strings use double quotes; json.dumps handles escaping


def _threshold(chk: Check) -> str:
    return "" if chk.min_compliance >= 1.0 else f" with threshold >= {chk.min_compliance}"


def rules_for(chk: Check) -> list[str]:
    """DQDL rules for one check; an empty list when DQDL has no clean equivalent.

    Ranges are written as two comparisons (>= and <=) rather than `between`, so both ends stay inclusive
    exactly like the Spark check.
    """
    p, c = chk.params, chk.column
    if chk.type == "size":
        out = [f"RowCount >= {int(p.get('min_rows', 1))}"]
        if p.get("max_rows") is not None:
            out.append(f"RowCount <= {int(p['max_rows'])}")
        return out
    if chk.type == "completeness":
        return [f"Completeness {_q(c)} >= {chk.min_compliance}"]
    if chk.type == "unique":
        return [f"Uniqueness {_q(c)} >= {chk.min_compliance}"]
    if chk.type == "range":
        out = []
        if "min" in p:
            out.append(f"ColumnValues {_q(c)} >= {p['min']}{_threshold(chk)}")
        if "max" in p:
            out.append(f"ColumnValues {_q(c)} <= {p['max']}{_threshold(chk)}")
        return out
    if chk.type == "allowed_values":
        vals = ", ".join(_q(str(v)) for v in p["values"])
        return [f"ColumnValues {_q(c)} in [{vals}]{_threshold(chk)}"]
    if chk.type == "pattern":
        return [f"ColumnValues {_q(c)} matches {_q(p['regex'])}{_threshold(chk)}"]
    if chk.type == "expression":
        if chk.min_compliance >= 1.0:
            return [f"CustomSql {_q('select count(*) from primary where not (' + p['expr'] + ')')} = 0"]
        sql = f"select avg(case when {p['expr']} then 1.0 else 0.0 end) from primary"
        return [f"CustomSql {_q(sql)} >= {chk.min_compliance}"]
    if chk.type == "row_count_change":
        pct = float(p["max_pct"])
        return [f"RowCount >= avg(last(1)) * {1 - pct:g}", f"RowCount <= avg(last(1)) * {1 + pct:g}"]
    return []


def to_dqdl(suite: Suite) -> tuple[str, list[str]]:
    """Returns (ruleset text, names of checks that were not translated)."""
    rules, skipped = [], []
    for chk in suite.checks:
        r = rules_for(chk)
        if r:
            rules.extend(r)
        else:
            skipped.append(chk.name)
    return "Rules = [\n    " + ",\n    ".join(rules) + "\n]\n", skipped
