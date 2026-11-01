from datetime import date

import pytest

from projection import MIN_BYTES_PER_QUERY, ProjectedTable, query_cost_usd

TABLE = ProjectedTable("bfsi_curated", "card_txn", "s3://bfsi-lake-dev/curated/card_txn",
                       [("txn_id", "string"), ("amount", "decimal(12,2)")], dt_start=date(2026, 10, 1))


def test_ddl_has_projection_properties():
    ddl = TABLE.ddl()
    assert "'projection.enabled' = 'true'" in ddl
    assert "'projection.dt.range' = '2026-10-01,NOW'" in ddl
    assert "'projection.region.values' = 'south,west,north,east'" in ddl
    assert "region=${region}/dt=${dt}/" in ddl
    assert "PARTITIONED BY (`region` string, `dt` string)" in ddl


def test_single_partition_prefix():
    p = TABLE.prefixes(date(2026, 10, 15), date(2026, 10, 15), ["south"], today=date(2026, 11, 1))
    assert p == ["s3://bfsi-lake-dev/curated/card_txn/region=south/dt=2026-10-15/"]


def test_range_without_region_reads_every_region():
    p = TABLE.prefixes(date(2026, 10, 1), date(2026, 10, 7), today=date(2026, 11, 1))
    assert len(p) == 7 * 4


def test_dates_outside_projection_range_are_ignored():
    p = TABLE.prefixes(date(2026, 9, 25), date(2026, 11, 5), ["west"], today=date(2026, 11, 1))
    assert p[0].endswith("dt=2026-10-01/") and p[-1].endswith("dt=2026-11-01/")
    assert len(p) == 32


def test_unknown_region_reads_nothing():
    assert TABLE.prefixes(date(2026, 10, 1), date(2026, 10, 2), ["central"], today=date(2026, 11, 1)) == []


def test_cost_has_10mb_minimum():
    assert query_cost_usd(1) == query_cost_usd(MIN_BYTES_PER_QUERY)
    assert query_cost_usd(1024 ** 4) == pytest.approx(5.0)
