from scan_compare import compare


def test_parquet_and_partitions_cut_bytes_scanned(tmp_path):
    res = compare(tmp_path, days=20, rows_per_day=2000)
    csv_b = res["A csv, no partitions"][0]
    flat_b = res["B parquet, no partitions"][0]
    part_b = res["C parquet, region/dt"][0]
    assert flat_b < csv_b * 0.25      # columnar + compression: only 3 of 10 columns read
    assert part_b < flat_b / 20       # one region-day out of 80 partitions
    # all of these are below the 10 MB floor, so they bill the same
    assert res["B parquet, no partitions"][1] == res["C parquet, region/dt"][1]
