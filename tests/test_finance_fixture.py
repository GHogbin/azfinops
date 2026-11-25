from datetime import date
from decimal import Decimal as D

import pytest

from azfinops.finance import (
    MissingRateError,
    MixedBasisError,
    convert_amount,
    cost_by_charge_type,
    coverage,
    filter_rows,
    observed_discount,
    price_volume,
    reservation_utilization,
    rnd,
    total_cost,
)

ELIGIBLE = {"Virtual Machines"}


def test_actual_totals_and_signed_components(actual, expected):
    e = expected["actual"]["USD"]
    assert rnd(total_cost(actual, "USD")) == D(e["total"])
    by = cost_by_charge_type(actual, "USD")
    assert {k: rnd(v) for k, v in by.items()} == {k: D(v) for k, v in e["by_charge_type"].items()}
    assert rnd(total_cost(actual, "GBP")) == D(expected["actual"]["GBP"]["total"])


def test_amortized_totals(amortized, expected):
    assert rnd(total_cost(amortized, "USD")) == D(expected["amortized"]["USD"]["total"])


def test_mixing_actual_and_amortized_is_refused(actual, amortized):
    with pytest.raises(MixedBasisError):
        total_cost(actual + amortized, "USD")


def test_native_currencies_never_summed(actual):
    assert total_cost(actual, "USD") != total_cost(actual, "GBP")


def test_reservation_utilization(amortized, expected):
    assert reservation_utilization(amortized, "USD") == D(expected["reservation_utilization"])


def test_utilization_requires_amortized(actual):
    with pytest.raises(MixedBasisError):
        reservation_utilization(actual, "USD")


def test_utilization_none_without_capacity(amortized):
    rows = [r for r in amortized if r["pricing_model"] != "Reservation"]
    assert reservation_utilization(rows, "USD") is None  # not 0%


def test_coverage_feb(actual, amortized, expected):
    e = expected["coverage_feb_usd"]
    for rows in (actual, amortized):  # coverage uses payg*qty so both bases agree
        feb = filter_rows(rows, date(2026, 2, 1), date(2026, 2, 28), "USD")
        c = coverage(feb, "USD", ELIGIBLE)
        assert rnd(c.covered) == D(e["covered"])
        assert rnd(c.eligible) == D(e["eligible"])
        assert rnd(c.ratio, 10) == D(e["ratio"])


def test_coverage_excludes_missing_price_rather_than_zero(amortized):
    feb = [dict(r) for r in filter_rows(amortized, date(2026, 2, 1), date(2026, 2, 28), "USD")]
    for r in feb:
        if r["meter_id"] == "M2":
            r["payg_price"] = ""
    c = coverage(feb, "USD", ELIGIBLE)
    assert c.excluded_missing_price == 1
    assert rnd(c.eligible) == D("35.00")  # M2's 1.00 neither counted as 0 nor as covered


def test_observed_discount(amortized, expected):
    e = expected["observed_discount_usd"]
    o = observed_discount(amortized, "USD")
    assert (rnd(o.payg_equivalent), rnd(o.gross), rnd(o.unused_cost), rnd(o.net)) == (
        D(e["payg_equivalent"]), D(e["gross"]), D(e["unused"]), D(e["net"]))


def test_price_volume_split(actual, expected):
    e = expected["price_volume_m1_jan_to_feb_usd"]
    m1 = [r for r in actual if r["meter_id"] == "M1"]
    jan = filter_rows(m1, date(2026, 1, 1), date(2026, 1, 31))
    feb = filter_rows(m1, date(2026, 2, 1), date(2026, 2, 28))
    pv = price_volume(jan, feb, "USD")
    assert (rnd(pv.change), rnd(pv.volume), rnd(pv.price)) == (
        D(e["change"]), D(e["volume"]), D(e["price"]))
    assert rnd(pv.volume + pv.price) == rnd(pv.change)


def test_price_volume_new_meters_are_not_split(actual):
    jan = filter_rows(actual, date(2026, 1, 1), date(2026, 1, 31), "USD")
    feb = filter_rows(actual, date(2026, 2, 1), date(2026, 2, 28), "USD")
    pv = price_volume(jan, feb, "USD")
    assert rnd(pv.new) == D("21.00")  # M2 1.00 + marketplace 20.00 (RI usage cost is 0)
    assert rnd(pv.volume + pv.price + pv.new + pv.removed + pv.unattributed) == rnd(pv.change)


def test_fx_is_presentation_only_and_missing_rate_raises(expected):
    f = expected["fx"]
    rates = {("GBP", "USD"): D(f["gbp_to_usd_rate"])}
    assert rnd(convert_amount(D(100), "GBP", "USD", rates)) == D(f["gbp_100_in_usd"])
    with pytest.raises(MissingRateError):
        convert_amount(D(100), "EUR", "USD", rates)


def _benefit_row(payg, basis=None, pm="Reservation", price="0.8"):
    return {"cost_kind": "amortized", "date": "2026-09-01", "billing_currency": "GBP",
            "charge_type": "Usage", "pricing_model": pm, "meter_category": "Virtual Machines",
            "quantity": "4", "payg_price": payg, "unit_price": price, "payg_basis": basis}


def test_zero_payg_on_reservation_row_is_unknown_not_free():
    rows = [_benefit_row("0")]  # real exports: PayGPrice 0 on reservation rows
    c = coverage(rows, "GBP", ELIGIBLE)
    assert c.excluded_missing_price == 1 and c.eligible == 0 and c.ratio is None
    assert observed_discount(rows, "GBP").excluded_missing_price == 1


def test_contract_basis_rows_are_valued_and_labelled():
    rows = [_benefit_row("0.8", "contract"), _benefit_row("0.15", "payg", "SavingsPlan"),
            {**_benefit_row("0.5", "payg", "On Demand"), "quantity": "10"}]
    c = coverage(rows, "GBP", ELIGIBLE)
    assert rnd(c.covered, 4) == D("3.8000")      # 4*0.8 + 4*0.15
    assert rnd(c.eligible, 4) == D("8.8000")      # + uncovered 10*0.5
    assert c.contract_basis_rows == 1 and c.excluded_missing_price == 0


def test_free_on_demand_meter_with_zero_payg_is_still_valid():
    r = {**_benefit_row("0", "payg", "On Demand"), "quantity": "3"}
    c = coverage([r], "GBP", ELIGIBLE)
    assert c.excluded_missing_price == 0 and c.eligible == 0
