"""Load and validate the Lake Formation policy file.

The YAML is the single place where tags, tag assignments, grants and data cell
filters are declared. Everything else (the boto3 plan, the access preview, the
audit) is derived from the validated Policy object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_PERMISSIONS = {"SELECT", "DESCRIBE", "ALTER", "INSERT", "DELETE", "DROP", "ALL"}


class PolicyError(ValueError):
    """Raised when the policy file is inconsistent."""


@dataclass
class Table:
    database: str
    name: str
    columns: list[str]
    column_tags: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class Database:
    name: str
    tags: dict[str, str]
    tables: dict[str, Table]


@dataclass
class TagGrant:
    principal: str
    expression: dict[str, list[str]]
    permissions: list[str]


@dataclass
class DataFilter:
    name: str
    database: str
    table: str
    row_filter: str | None
    columns: list[str] | None
    excluded_columns: list[str] | None
    grant_to: list[str]


@dataclass
class Policy:
    account_id: str
    lf_tags: dict[str, list[str]]
    databases: dict[str, Database]
    principals: dict[str, str]
    admins: list[str]
    tag_grants: list[TagGrant]
    data_filters: list[DataFilter]

    def table(self, database: str, table: str) -> Table:
        try:
            return self.databases[database].tables[table]
        except KeyError:
            raise PolicyError(f"unknown table {database}.{table}") from None

    def column_tags(self, database: str, table: str) -> dict[str, dict[str, str]]:
        """Effective tags per column after inheritance: database -> table -> column override."""
        db = self.databases[database]
        t = self.table(database, table)
        out = {}
        for col in t.columns:
            tags = dict(db.tags)
            tags.update(t.column_tags.get(col, {}))
            out[col] = tags
        return out

    def principal_arn(self, name: str) -> str:
        return self.principals[name]


def _check_tags(tags: dict, lf_tags: dict, where: str) -> None:
    for key, value in tags.items():
        if key not in lf_tags:
            raise PolicyError(f"{where}: tag key '{key}' is not defined in lf_tags")
        if value not in lf_tags[key]:
            raise PolicyError(f"{where}: value '{value}' is not allowed for tag '{key}'")


def parse(raw: dict) -> Policy:
    lf_tags = {k: list(v) for k, v in (raw.get("lf_tags") or {}).items()}
    principals = dict(raw.get("principals") or {})
    if not principals:
        raise PolicyError("no principals defined")

    databases = {}
    for db_name, db_raw in (raw.get("databases") or {}).items():
        db_tags = dict(db_raw.get("tags") or {})
        _check_tags(db_tags, lf_tags, f"database {db_name}")
        tables = {}
        for t_name, t_raw in (db_raw.get("tables") or {}).items():
            columns = list(t_raw.get("columns") or [])
            if len(columns) != len(set(columns)):
                raise PolicyError(f"{db_name}.{t_name}: duplicate column names")
            col_tags = {c: dict(v) for c, v in (t_raw.get("column_tags") or {}).items()}
            for col, tags in col_tags.items():
                if col not in columns:
                    raise PolicyError(f"{db_name}.{t_name}: tags on unknown column '{col}'")
                _check_tags(tags, lf_tags, f"{db_name}.{t_name}.{col}")
            tables[t_name] = Table(db_name, t_name, columns, col_tags)
        databases[db_name] = Database(db_name, db_tags, tables)

    admins = list(raw.get("admins") or [])
    for a in admins:
        if a not in principals:
            raise PolicyError(f"admin '{a}' is not a defined principal")

    grants = []
    for i, g in enumerate(raw.get("tag_grants") or []):
        where = f"tag_grants[{i}]"
        if g["principal"] not in principals:
            raise PolicyError(f"{where}: unknown principal '{g['principal']}'")
        expr = {k: list(v) for k, v in g["expression"].items()}
        for key, values in expr.items():
            for v in values:
                _check_tags({key: v}, lf_tags, where)
        perms = [p.upper() for p in g["permissions"]]
        bad = set(perms) - VALID_PERMISSIONS
        if bad:
            raise PolicyError(f"{where}: unknown permissions {sorted(bad)}")
        grants.append(TagGrant(g["principal"], expr, perms))

    filters = []
    seen = set()
    for f in raw.get("data_filters") or []:
        name = f["name"]
        if name in seen:
            raise PolicyError(f"duplicate data filter name '{name}'")
        seen.add(name)
        if f["database"] not in databases or f["table"] not in databases[f["database"]].tables:
            raise PolicyError(f"filter {name}: unknown table {f['database']}.{f['table']}")
        table_cols = databases[f["database"]].tables[f["table"]].columns
        cols, excluded = f.get("columns"), f.get("excluded_columns")
        if cols and excluded:
            raise PolicyError(f"filter {name}: use columns or excluded_columns, not both")
        for c in (cols or []) + (excluded or []):
            if c not in table_cols:
                raise PolicyError(f"filter {name}: unknown column '{c}'")
        for p in f.get("grant_to") or []:
            if p not in principals:
                raise PolicyError(f"filter {name}: unknown principal '{p}'")
        filters.append(DataFilter(name, f["database"], f["table"], f.get("row_filter"),
                                  cols, excluded, list(f.get("grant_to") or [])))

    return Policy(str(raw["account_id"]), lf_tags, databases, principals, admins, grants, filters)


def load(path: str | Path) -> Policy:
    with open(path) as fh:
        return parse(yaml.safe_load(fh))
