"""Month-over-month movement against rows inserted by hand (needs duckdb). Expected values are derived by hand:

            Aug (q, cost)    Sep (q, cost)
  M1 1 Hour   100, 10         150, 18     p0 = 0.10   volume (150-100)*0.10 = 5    price 18 - 150*0.10 = 3    change 8
  M2 1 GB      10,  5           8,  3     p0 = 0.50   volume (8-10)*0.50   = -1   price 3 - 8*0.50    = -1   change -2
  M3            -               1,  4     new meter                                                           change 4
  M4 1 Hour     1,  6           -         removed meter                                                       change -6
  M5 1 Hour     0,  1           5,  2     zero quantity in Aug: no unit price, not split                      change 1
  total change = 27 - 22 = 5 = volume 4 + price 2 + new 4 + removed -6 + no-unit-price 1
A Purchase row and a USD row exist in both months and must be ignored.
"""
from datetime import date
from decimal import Decimal as D

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import queries as q
from azfinops.migrations.runner import migrate

AUG, SEP = date(2026, 8, 1), date(2026, 9, 1)
F = q.Filters("amortized", "GBP")


def _row(month, meter, unit, qty, cost, charge="Usage", ccy="GBP", name=None, cat="Compute", sub="s1", app="a1"):
    return (month, "amortized", ccy, sub, "rg", "r1", cat, meter, name or f"name-{meter}", unit, charge,
            "On Demand", None, None, app, "prod", cost, qty, 1, False)


ROWS = [
    _row(AUG, "M1", "1 Hour", 100, 10), _row(SEP, "M1", "1 Hour", 150, 18),
    _row(AUG, "M2", "1 GB", 10, 5, cat="Storage"), _row(SEP, "M2", "1 GB", 8, 3, cat="Storage"),
    _row(SEP, "M3", "1 Hour", 1, 4),
    _row(AUG, "M4", "1 Hour", 1, 6),
    _row(AUG, "M5", "1 Hour", 0, 1), _row(SEP, "M5", "1 Hour", 5, 2),
    _row(AUG, "P1", "1 Each", 1, 1000, charge="Purchase"), _row(SEP, "P1", "1 Each", 1, 2000, charge="Purchase"),
    _row(AUG, "U1", "1 Hour", 1, 99, ccy="USD"), _row(SEP, "U1", "1 Hour", 1, 77, ccy="USD"),
]


@pytest.fixture
def con(tmp_path):
    p = tmp_path / "m.duckdb"
    migrate(p)
    c = duckdb.connect(str(p))
    c.executemany("INSERT INTO agg_resource_month (usage_month, cost_kind, billing_currency, subscription_id, "
                  "resource_group, resource_id, meter_category, meter_id, meter_name, unit, charge_type, "
                  "pricing_model, commitment_id, reservation_name, application, environment, cost, quantity, "
                  "row_count, is_synthetic) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ROWS)
    for month, days in ((AUG, 31), (SEP, 30)):
        for currency in ("GBP", "USD"):
            c.execute("INSERT INTO aggregate_scope_freshness VALUES "
                      "(?, 'amortized', 'a1', ?, ?, ?, ?, 1, true, now())",
                      [month, currency, month, month.replace(day=days), days])
    yield c
    c.close()


def test_decomposition_matches_hand_derivation_and_reconciles(con):
    m = q.movement(con, F, AUG, SEP)
    assert m["available"] and m["groups"] == 5 and m["reconciles"] is True
    assert (m["change"], m["volume"], m["price"], m["new"], m["removed"], m["no_unit_price"]) == (
        D(5), D(4), D(2), D(4), D(-6), D(1))
    by = {r["meter_id"]: r for r in m["rows"]}
    assert (by["M1"]["kind"], by["M1"]["change"], by["M1"]["volume"], by["M1"]["price"]) == ("price_volume", D(8), D(5), D(3))
    assert (by["M2"]["change"], by["M2"]["volume"], by["M2"]["price"]) == (D(-2), D(-1), D(-1))
    assert (by["M3"]["kind"], by["M3"]["change"]) == ("new", D(4))
    assert (by["M4"]["kind"], by["M4"]["change"]) == ("removed", D(-6))
    assert (by["M5"]["kind"], by["M5"]["change"], by["M5"]["volume"]) == ("no_unit_price", D(1), None)
    assert m["other"] == D(0)


def test_ranked_by_size_and_truncation_still_adds_up(con):
    m = q.movement(con, F, AUG, SEP, n=3)
    assert [r["meter_id"] for r in m["rows"]] == ["M1", "M4", "M3"]         # |8| > |-6| > |4|
    assert m["other"] == D(-1)                                            # M2 (-2) + M5 (+1)
    assert sum((r["change"] for r in m["rows"]), D(0)) + m["other"] == m["change"]


def test_direction_reverses_cleanly(con):
    m = q.movement(con, F, SEP, AUG)
    assert m["change"] == D(-5) and m["reconciles"] is True
    by = {r["meter_id"]: r for r in m["rows"]}
    assert by["M3"]["kind"] == "removed" and by["M4"]["kind"] == "new"


def test_filters_narrow_the_comparison_and_unavailable_filters_are_reported(con):
    storage = q.movement(con, q.Filters("amortized", "GBP", services=("Storage",)), AUG, SEP)
    assert storage["groups"] == 1 and storage["change"] == D(-2)
    other_sub = q.movement(con, q.Filters("amortized", "GBP", subscription_ids=("nope",)), AUG, SEP)
    assert other_sub["available"] is False and "No usage" in other_sub["reason"]
    r = q.movement(con, q.Filters("amortized", "GBP", regions=("uksouth",), publisher_types=("Azure",)), AUG, SEP)
    assert r["filters_not_applied"] == ["regions", "publisher_types"]


def test_missing_month_and_invalid_months(con):
    assert q.movement(con, F, date(2026, 7, 1), SEP)["available"] is False
    for a, b in ((date(2026, 8, 15), SEP), (AUG, AUG)):
        with pytest.raises(ValueError):
            q.movement(con, F, a, b)
