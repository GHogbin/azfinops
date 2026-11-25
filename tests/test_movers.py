"""What changed: the biggest increases and decreases between two whole months."""
from datetime import date
from decimal import Decimal

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import combined
from azfinops import queries as q
from azfinops.aggregates import _build_scope_freshness
from azfinops.migrations.runner import migrate

COLS = ("usage_date, usage_month, cost_kind, billing_account_id, billing_currency, meter_category, charge_type, "
        "application, environment, cost, payg_equivalent, cost_with_payg, payg_missing_rows, contract_basis_rows, "
        "row_count, is_synthetic")


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "m.duckdb"
    migrate(db)
    c = duckdb.connect(str(db))
    rows = [  # (day, service, cost, currency)
        (date(2026, 8, 5), "Compute", 100, "GBP"), (date(2026, 8, 6), "Storage", 50, "GBP"),
        (date(2026, 9, 5), "Compute", 160, "GBP"), (date(2026, 9, 6), "Storage", 20, "GBP"),
        (date(2026, 9, 7), "Network", 30, "GBP"), (date(2026, 9, 8), "Compute", 100, "USD"),
    ]
    for d, svc, cost, ccy in rows:
        c.execute(f"INSERT INTO agg_daily ({COLS}) VALUES (?, ?, 'amortized', ?, ?, ?, 'Usage', 'a', 'e', ?, ?, ?, 0, 0, 1, false)",
                  [d, date(d.year, d.month, 1), "acctG" if ccy == "GBP" else "acctU", ccy, svc, cost, cost, cost])
    for month, days in ((date(2026, 8, 1), 31), (date(2026, 9, 1), 30)):
        for account, currency in (("acctG", "GBP"), ("acctU", "USD")):
            for day in range(1, days + 1):
                c.execute(f"INSERT INTO agg_daily ({COLS}) VALUES (?, ?, 'amortized', ?, ?, 'Compute', "
                          "'Usage', 'a', 'e', 0, 0, 0, 0, 0, 1, false)",
                          [month.replace(day=day), month, account, currency])
        _build_scope_freshness(c, month)
    yield c
    c.close()


def test_one_month_is_compared_with_the_month_before(con):
    r = q.movers(con, q.Filters("amortized", "GBP", start=date(2026, 9, 1), end=date(2026, 9, 30)), "service")
    assert r["available"] and (r["month_from"], r["month_to"]) == (date(2026, 8, 1), date(2026, 9, 1))
    assert [(i["key"], i["change"]) for i in r["increases"]] == [("Compute", Decimal(60)), ("Network", Decimal(30))]
    assert [(i["key"], i["change"]) for i in r["decreases"]] == [("Storage", Decimal(-30))]
    assert r["total_from"] == Decimal(150) and r["total_to"] == Decimal(210) and r["change"] == Decimal(60)
    assert r["increases"][1]["share"] is None                      # new in September: no percentage


def test_a_range_compares_its_last_two_months_whole(con):
    r = q.movers(con, q.Filters("amortized", "GBP", start=date(2026, 8, 6), end=date(2026, 9, 5)), "service")
    assert (r["month_from"], r["month_to"]) == (date(2026, 8, 1), date(2026, 9, 1))
    assert r["total_from"] == Decimal(150) and r["total_to"] == Decimal(210)


def test_the_combined_view_converts_before_comparing(con):
    combined.install(con, "GBP", [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.8"}])
    r = q.movers(con, q.Filters("amortized", "GBP", combined=True), "service")
    assert r["increases"][0] == {**r["increases"][0], "key": "Compute", "change": Decimal(140)}   # 160 + 100 * 0.8 - 100


def test_no_cost_and_bad_dimension(con):
    assert not q.movers(con, q.Filters("amortized", "EUR"), "service")["available"]
    with pytest.raises(ValueError):
        q.movers(con, q.Filters("amortized", "GBP"), "nope")


def test_partial_previous_month_is_not_a_zero_cost_baseline(con):
    con.execute("UPDATE aggregate_scope_freshness SET period_complete = false WHERE usage_month = DATE '2026-08-01'")
    result = q.movers(con, q.Filters("amortized", "GBP"))
    assert result["available"] is False and "complete months" in result["reason"]
