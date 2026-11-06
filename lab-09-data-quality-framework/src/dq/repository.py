"""Metrics repository: one JSON line per (dataset, run_date), like Deequ's FileSystemMetricsRepository.

Anomaly checks read their baseline from here. Runs that FAILED are stored (so you can see them) but are
left out of the baseline, otherwise one bad batch would shift what "normal" looks like for the next runs.
On AWS the same file sits on S3, or the metrics go to a small DynamoDB/Iceberg table.
"""
from __future__ import annotations

import json
from pathlib import Path


class MetricsRepository:
    def __init__(self, path):
        self.path = Path(path)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def save(self, dataset: str, run_date: str, status: str, metrics: dict) -> None:
        """Insert or replace the run, so re-running a day does not count it twice."""
        rows = [r for r in self._load() if not (r["dataset"] == dataset and r["run_date"] == run_date)]
        rows.append({"dataset": dataset, "run_date": run_date, "status": status, "metrics": metrics})
        rows.sort(key=lambda r: (r["dataset"], r["run_date"]))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(self.path)

    def history(self, dataset: str, before: str, include_failed: bool = False) -> list[dict]:
        """Earlier runs of a dataset, oldest first."""
        return [r for r in self._load()
                if r["dataset"] == dataset and r["run_date"] < before
                and (include_failed or r["status"] != "FAIL")]
