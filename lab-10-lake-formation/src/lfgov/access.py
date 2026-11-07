"""Work out what a principal can see in a table, and preview it on local CSV rows.

This answers "what does the risk analyst actually get when they run SELECT * on
customers?" before anything is applied in the account, so a policy change can be
reviewed like code.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass

from .policy import Policy


@dataclass
class AccessPath:
    source: str                 # "tag:<expr>" or "filter:<name>"
    columns: list[str]          # visible columns, in table order
    row_filter: str | None      # None = all rows
    permissions: list[str]


def tags_match(tags: dict[str, str], expression: dict[str, list[str]]) -> bool:
    """Keys are AND, values inside one key are OR. A missing key never matches."""
    return all(tags.get(key) in values for key, values in expression.items())


def access_paths(policy: Policy, principal: str, database: str, table: str) -> list[AccessPath]:
    t = policy.table(database, table)
    col_tags = policy.column_tags(database, table)
    paths = []
    for g in policy.tag_grants:
        if g.principal != principal:
            continue
        cols = [c for c in t.columns if tags_match(col_tags[c], g.expression)]
        if cols:
            expr = ";".join(f"{k}={'|'.join(v)}" for k, v in g.expression.items())
            paths.append(AccessPath(f"tag:{expr}", cols, None, list(g.permissions)))
    for f in policy.data_filters:
        if principal not in f.grant_to or (f.database, f.table) != (database, table):
            continue
        if f.columns:
            cols = [c for c in t.columns if c in f.columns]
        else:
            excluded = set(f.excluded_columns or [])
            cols = [c for c in t.columns if c not in excluded]
        paths.append(AccessPath(f"filter:{f.name}", cols, f.row_filter, ["SELECT"]))
    return paths


# --- row filter evaluation (the subset of PartiQL used in data cell filters) ---

_CLAUSE = re.compile(
    r"^\s*(?P<col>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:(?P<op>=|!=|<>|>=|<=|>|<)\s*(?P<val>'[^']*'|-?\d+(?:\.\d+)?)"
    r"|(?P<neg>NOT\s+)?IN\s*\((?P<list>[^)]*)\))\s*$",
    re.IGNORECASE,
)
_AND = re.compile(r"\s+AND\s+(?=(?:[^']*'[^']*')*[^']*$)", re.IGNORECASE)


class FilterSyntaxError(ValueError):
    pass


def _literal(token: str):
    token = token.strip()
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1]
    try:
        return float(token)
    except ValueError:
        raise FilterSyntaxError(f"cannot read literal {token!r}") from None


def _cast(cell: str, like):
    if isinstance(like, float):
        try:
            return float(cell)
        except (TypeError, ValueError):
            return None
    return cell


def compile_filter(expr: str | None):
    """Return a predicate row(dict) -> bool. Supports AND of =, !=, <>, <, <=, >, >=, IN, NOT IN."""
    if expr is None or expr.strip().upper() in ("", "TRUE"):
        return lambda row: True
    preds = []
    for clause in _AND.split(expr.strip()):
        m = _CLAUSE.match(clause)
        if not m:
            raise FilterSyntaxError(f"unsupported clause: {clause!r}")
        col = m.group("col")
        if m.group("list") is not None:
            values = [_literal(v) for v in m.group("list").split(",")]
            negate = bool(m.group("neg"))

            def pred(row, col=col, values=values, negate=negate):
                cell = row.get(col)
                if cell in (None, ""):
                    return False
                hit = _cast(cell, values[0]) in values
                return hit != negate
        else:
            op, val = m.group("op"), _literal(m.group("val"))

            def pred(row, col=col, op=op, val=val):
                cell = row.get(col)
                if cell in (None, ""):
                    return False  # SQL: comparisons with NULL are not true
                v = _cast(cell, val)
                if v is None:
                    return False
                return {"=": v == val, "!=": v != val, "<>": v != val, ">": v > val,
                        ">=": v >= val, "<": v < val, "<=": v <= val}[op]
        preds.append(pred)
    return lambda row: all(p(row) for p in preds)


class AmbiguousAccess(Exception):
    """More than one path grants SELECT; Lake Formation unions them, which a preview should not guess."""


def preview(policy: Policy, principal: str, database: str, table: str, csv_path: str) -> dict:
    paths = [p for p in access_paths(policy, principal, database, table) if "SELECT" in p.permissions
             or "ALL" in p.permissions]
    if not paths:
        return {"principal": principal, "table": f"{database}.{table}", "access": "none",
                "columns": [], "rows": []}
    if len(paths) > 1:
        raise AmbiguousAccess(f"{principal} has {len(paths)} SELECT paths on {database}.{table}: "
                              + ", ".join(p.source for p in paths))
    path = paths[0]
    keep = compile_filter(path.row_filter)
    with open(csv_path, newline="") as fh:
        rows = [{c: r[c] for c in path.columns} for r in csv.DictReader(fh) if keep(r)]
    return {"principal": principal, "table": f"{database}.{table}", "access": path.source,
            "columns": path.columns, "row_filter": path.row_filter, "rows": rows}
