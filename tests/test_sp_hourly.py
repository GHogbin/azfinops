"""Savings-plan-eligible usage per hour not covered by a plan (hand-computed below)."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import queries as q
from azfinops.migrations.runner import migrate

COLS = ("usage_date, usage_month, cost_kind, billing_account_id, billing_currency, meter_category, charge_type, "
        "pricing_model, publisher_type, application, environment, cost, payg_equivalent, cost_with_payg, "
        "payg_missing_rows, contract_basis_rows, row_count, is_synthetic")
LAST = date(2026, 9, 30)


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "s.duckdb"
    migrate(db)
    c = duckdb.connect(str(db))

    def add(day, cat, ct, pm, cost, pub=None):
        c.execute(f"INSERT INTO agg_daily ({COLS}) VALUES (?, ?, 'amortized', 'a1', 'GBP', ?, ?, ?, ?, 'x', 'e', ?, ?, ?, 0, 0, 1, false)",
                  [day, day.replace(day=1), cat, ct, pm, pub, cost, cost, cost])

    for i in range(10):                                   # 21 to 30 September
        day = LAST - timedelta(days=i)
        add(day, "Virtual Machines", "Usage", "On Demand", 240 if i else 480)   # 10/hour, 20/hour on the last day
        add(day, "Virtual Machines", "Usage", "SavingsPlan", 120)               # covered: 5/hour
        add(day, "Virtual Machines", "UnusedSavingsPlan", "SavingsPlan", 24)    # unused: 1/hour
        add(day, "Storage", "Usage", "On Demand", 999)                          # not eligible
        add(day, "Virtual Machines", "Usage", "Spot", 50)                       # spot: left out
        add(day, "Virtual Machines", "Usage", "On Demand", 77, "Marketplace")   # marketplace: left out
    add(date(2026, 9, 25), "Azure Example", "Usage", "SavingsPlan", 0)          # a category seen with plan usage
    add(date(2026, 9, 25), "Azure Example", "Usage", "On Demand", 48)           # so this counts: 2/hour that day
    yield c
    c.close()


def test_hourly_uncovered_now_recent_and_baseline(con):
    r = q.sp_hourly(con, q.Filters("amortized", "GBP"))
    assert r["available"] and r["last_day"] == LAST
    assert r["uncovered_last_day"] == D(20)
    assert r["uncovered_7d"] == (D(20) + 5 * D(10) + D(12)) / 7     # 24–30 Sep; the 25th has 2/hour more
    assert r["days_30d"] == 10 and r["uncovered_30d"] == (D(20) + 9 * D(10) + D(2)) / 10
    assert r["baseline_30d"] == D(10) and r["baseline_day"] == date(2026, 9, 21)    # lowest day; earliest on a tie
    assert r["covered_7d"] == D(5) and r["unused_7d"] == D(1)
    assert "Azure Example" in r["categories"] and r["categories_seen"] == ["Azure Example", "Virtual Machines"]
    assert [x["category"] for x in r["by_category_7d"]] == ["Virtual Machines", "Azure Example"]
    assert r["by_category_7d"][0]["per_hour"] == (D(480) + 6 * D(240)) / (24 * 7)
    assert len(r["daily"]) == 10


def test_no_cost(con):
    assert not q.sp_hourly(con, q.Filters("amortized", "EUR"))["available"]
