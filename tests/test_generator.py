import csv
from decimal import Decimal as D

import pytest

from azfinops.canonical import COLUMNS
from azfinops.finance import MixedBasisError, total_cost
from azfinops.generator import generate_dataset


def _rows(path, kind):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["cost_kind"] = kind
    return rows


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    out = tmp_path_factory.mktemp("syn")
    m = generate_dataset(out, seed=7, months=13, scale=0.5)
    return out, m


def test_deterministic(tmp_path, ds):
    _, m1 = ds
    m2 = generate_dataset(tmp_path, seed=7, months=13, scale=0.5)
    assert m1["content_sha256"] == m2["content_sha256"]
    m3 = generate_dataset(tmp_path / "other", seed=8, months=13, scale=0.5)
    assert m3["content_sha256"] != m1["content_sha256"]


def test_everything_is_labelled_synthetic(ds):
    out, m = ds
    assert m["synthetic"] is True
    for k in ("actual", "amortized"):
        rows = _rows(out / f"{k}.csv", k)
        assert rows and all(r["x_synthetic"] == "true" for r in rows)
        assert list(rows[0].keys())[:-1] == COLUMNS


def test_at_least_12_months_and_charge_variety(ds):
    out, _ = ds
    a = _rows(out / "actual.csv", "actual")
    months = {r["date"][:7] for r in a}
    assert len(months) >= 12
    kinds = {r["charge_type"] for r in a}
    assert {"Usage", "Purchase", "Refund", "Credit", "Adjustment"} <= kinds
    assert {r["billing_currency"] for r in a} == {"USD", "GBP"}
    assert {r["pricing_model"] for r in a if r["charge_type"] == "Usage"} >= {
        "On Demand", "Spot", "Reservation", "SavingsPlan"}
    assert any(r["publisher_type"] == "Marketplace" for r in a)


def test_amortized_has_unused_rows_and_no_purchases(ds):
    out, _ = ds
    m = _rows(out / "amortized.csv", "amortized")
    kinds = {r["charge_type"] for r in m}
    assert "UnusedReservation" in kinds and "UnusedSavingsPlan" in kinds
    assert not kinds & {"Purchase", "Refund", "Credit", "Adjustment"}


def test_reservation_amortization_invariant(ds):
    """used + unused amortized cost per day == purchase / 365, so one term sums to the purchase."""
    out, _ = ds
    a = _rows(out / "actual.csv", "actual")
    m = _rows(out / "amortized.csv", "amortized")
    purchase = sum(D(r["cost"]) for r in a if r["reservation_id"] == "syn-ri-1" and r["charge_type"] == "Purchase")
    per_day: dict[str, D] = {}
    for r in m:
        if r["reservation_id"] == "syn-ri-1":
            per_day[r["date"]] = per_day.get(r["date"], D(0)) + D(r["cost"])
    assert len(per_day) > 300
    for v in per_day.values():
        assert abs(v * 365 - purchase) < D("0.01")


def test_savings_plan_monthly_charges_match_amortized_daily(ds):
    out, _ = ds
    a = _rows(out / "actual.csv", "actual")
    m = _rows(out / "amortized.csv", "amortized")
    for mon in sorted({r["date"][:7] for r in m if r["benefit_id"] == "syn-sp-1"})[1:-1]:
        charged = sum(D(r["cost"]) for r in a if r["benefit_id"] == "syn-sp-1"
                      and r["charge_type"] == "Purchase" and r["date"][:7] == mon)
        amort = sum(D(r["cost"]) for r in m if r["benefit_id"] == "syn-sp-1" and r["date"][:7] == mon)
        assert abs(charged - amort) < D("0.01"), mon


def test_actual_and_amortized_never_combine(ds):
    out, _ = ds
    a = _rows(out / "actual.csv", "actual")
    m = _rows(out / "amortized.csv", "amortized")
    with pytest.raises(MixedBasisError):
        total_cost(a + m, "USD")
    assert total_cost(a, "USD") != total_cost(m, "USD")
