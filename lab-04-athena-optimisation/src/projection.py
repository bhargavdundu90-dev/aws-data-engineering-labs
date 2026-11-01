"""Partition projection helpers for Athena.

With partition projection Athena works out partition locations from table
properties instead of calling the Glue catalog (no MSCK REPAIR, no crawler,
no GetPartitions latency on tables with thousands of partitions).

This module:
  * renders the CREATE EXTERNAL TABLE DDL with projection properties
  * expands a dt range / region filter into the exact S3 prefixes Athena
    would read, which is how pruning is reasoned about and tested
  * estimates query cost from bytes scanned
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

PRICE_PER_TB_USD = 5.0          # Athena SQL, per TB scanned
MIN_BYTES_PER_QUERY = 10 * 1024 ** 2  # each query is billed for at least 10 MB
TB = 1024 ** 4


@dataclass
class ProjectedTable:
    database: str
    name: str
    location: str                      # s3://bucket/prefix (no trailing slash)
    columns: list[tuple[str, str]]
    dt_start: date
    regions: list[str] = field(default_factory=lambda: ["south", "west", "north", "east"])

    @property
    def template(self) -> str:
        return f"{self.location}/region=${{region}}/dt=${{dt}}/"

    def properties(self) -> dict[str, str]:
        return {
            "projection.enabled": "true",
            "projection.dt.type": "date",
            "projection.dt.format": "yyyy-MM-dd",
            "projection.dt.range": f"{self.dt_start.isoformat()},NOW",
            "projection.dt.interval": "1",
            "projection.dt.interval.unit": "DAYS",
            "projection.region.type": "enum",
            "projection.region.values": ",".join(self.regions),
            "storage.location.template": self.template,
            "parquet.compression": "SNAPPY",
        }

    def ddl(self) -> str:
        cols = ",\n".join(f"  `{c}` {t}" for c, t in self.columns)
        props = ",\n".join(f"  '{k}' = '{v}'" for k, v in self.properties().items())
        return (
            f"CREATE EXTERNAL TABLE IF NOT EXISTS {self.database}.{self.name} (\n{cols}\n)\n"
            f"PARTITIONED BY (`region` string, `dt` string)\n"
            f"STORED AS PARQUET\n"
            f"LOCATION '{self.location}/'\n"
            f"TBLPROPERTIES (\n{props}\n);"
        )

    def prefixes(self, dt_from: date, dt_to: date, regions: list[str] | None = None,
                 today: date | None = None) -> list[str]:
        """S3 prefixes read for `dt BETWEEN dt_from AND dt_to [AND region IN (...)]`.

        Dates outside the projection range (before dt_start or after today) produce
        no prefixes, the same way Athena simply finds no partition there.
        """
        today = today or date.today()
        lo, hi = max(dt_from, self.dt_start), min(dt_to, today)
        wanted = self.regions if regions is None else [r for r in regions if r in self.regions]
        out = []
        d = lo
        while d <= hi:
            out += [self.template.replace("${region}", r).replace("${dt}", d.isoformat()) for r in wanted]
            d += timedelta(days=1)
        return out


def query_cost_usd(bytes_scanned: int) -> float:
    billed = max(bytes_scanned, MIN_BYTES_PER_QUERY)
    return billed / TB * PRICE_PER_TB_USD
