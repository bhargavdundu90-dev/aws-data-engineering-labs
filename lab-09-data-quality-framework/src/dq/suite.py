"""Load and validate a YAML check suite.

A suite lists checks for one dataset. Three kinds:

* row checks (completeness, unique, range, allowed_values, pattern, expression) are evaluated per row;
  failing rows are quarantined and the batch fails only when compliance drops below ``min_compliance``
* dataset checks (size) look at the batch as a whole
* anomaly checks (row_count_change, metric_zscore) compare this batch with earlier runs from the
  metrics repository
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROW_TYPES = {"completeness", "unique", "range", "allowed_values", "pattern", "expression"}
DATASET_TYPES = {"size"}
ANOMALY_TYPES = {"row_count_change", "metric_zscore"}
LEVELS = {"error", "warn"}

# keys each type needs besides name/type/level
REQUIRED = {
    "completeness": {"column"},
    "unique": {"column"},
    "range": {"column"},
    "allowed_values": {"column", "values"},
    "pattern": {"column", "regex"},
    "expression": {"expr"},
    "size": set(),
    "row_count_change": {"max_pct"},
    "metric_zscore": {"column", "max_z"},
}


class SuiteError(ValueError):
    pass


@dataclass(frozen=True)
class Check:
    name: str
    type: str
    level: str = "error"
    column: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def is_row_check(self) -> bool:
        return self.type in ROW_TYPES

    @property
    def min_compliance(self) -> float:
        return float(self.params.get("min_compliance", 1.0))


@dataclass(frozen=True)
class Suite:
    dataset: str
    key: str
    checks: tuple[Check, ...]

    def row_checks(self) -> list[Check]:
        return [c for c in self.checks if c.is_row_check]


def parse_suite(doc: dict) -> Suite:
    for k in ("dataset", "key", "checks"):
        if k not in doc:
            raise SuiteError(f"suite is missing '{k}'")
    checks, seen = [], set()
    for i, raw in enumerate(doc["checks"]):
        raw = dict(raw)
        ctype = raw.pop("type", None)
        if ctype not in REQUIRED:
            raise SuiteError(f"check #{i}: unknown type {ctype!r}")
        name = raw.pop("name", None) or f"{ctype}_{raw.get('column', i)}"
        if name in seen:
            raise SuiteError(f"duplicate check name {name!r}")
        seen.add(name)
        level = raw.pop("level", "error")
        if level not in LEVELS:
            raise SuiteError(f"{name}: level must be error or warn, got {level!r}")
        missing = REQUIRED[ctype] - raw.keys()
        if missing:
            raise SuiteError(f"{name}: missing {sorted(missing)}")
        mc = raw.get("min_compliance", 1.0)
        if not 0.0 <= float(mc) <= 1.0:
            raise SuiteError(f"{name}: min_compliance must be between 0 and 1")
        column = raw.pop("column", None)
        checks.append(Check(name=name, type=ctype, level=level, column=column, params=raw))
    return Suite(dataset=doc["dataset"], key=doc["key"], checks=tuple(checks))


def load_suite(path) -> Suite:
    return parse_suite(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
