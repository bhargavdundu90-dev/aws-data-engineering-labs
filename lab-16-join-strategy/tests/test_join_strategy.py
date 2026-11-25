import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pytest
from join_strategy import choose_strategy, skew_ratio, salt_key

MB = 1024 * 1024


def test_small_dimension_is_broadcast():
    assert choose_strategy(50_000 * MB, 5 * MB) == "broadcast"


def test_skewed_large_join_is_salted():
    keys = ["MERCHANT_1"] * 80 + ["m2"] * 20
    assert choose_strategy(500 * MB, 400 * MB, keys) == "salted_shuffle"


def test_even_large_join_is_sort_merge():
    keys = [f"k{i}" for i in range(100)]
    assert choose_strategy(500 * MB, 400 * MB, keys) == "sort_merge"


def test_skew_ratio_empty_and_negative():
    assert skew_ratio([]) == 0.0
    with pytest.raises(ValueError):
        choose_strategy(-1, 5)


def test_salt_key_spreads():
    assert {salt_key("k", i, 4) for i in range(8)} == {f"k#{i}" for i in range(4)}
