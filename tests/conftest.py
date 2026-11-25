import csv
import json
import os
from pathlib import Path

import pytest

# The invented fixtures and their hand-derived numbers use the export's PayGPrice column; production does not.
os.environ.setdefault("AZFINOPS_USE_PAYG_PRICE", "1")

FIX = Path(__file__).parent / "fixtures" / "reconciled"


def load(name: str) -> list[dict]:
    kind = name
    with open(FIX / f"{name}.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["cost_kind"] = kind
    return rows


@pytest.fixture(scope="session")
def actual() -> list[dict]:
    return load("actual")


@pytest.fixture(scope="session")
def amortized() -> list[dict]:
    return load("amortized")


@pytest.fixture(scope="session")
def expected() -> dict:
    return json.loads((FIX / "expected.json").read_text())
