from pathlib import Path

import pytest

from lfgov.policy import load

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root():
    return ROOT


@pytest.fixture
def policy():
    return load(ROOT / "policy" / "lake_policy.yml")
