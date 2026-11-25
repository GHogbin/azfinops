"""Cost analysis: series by dimension, Others, gaps filled, previous period, budgets and saved views."""
from datetime import date
from decimal import Decimal as D

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import prefs
from azfinops import queries as q
from azfinops.migrations.runner import migrate

COLS = ("usage_date, usage_month, cost_kind, billing_account_id, billing_currency, meter_category, charge_type, "
        "application, environment, cost, payg_equivalent, cost_with_payg, payg_missing_rows, contract_basis_rows, "
        "row_count, is_synthetic")
ROWS = [  # (day, service, cost)
    (date(2026, 8, 30), "VM", 5), (date(2026, 9, 1), "VM", 10), (date(2026, 9, 1), "SQL", 4),
    (date(2026, 9, 3), "VM", 12), (date(2026, 9, 3), "Web", 2), (date(2026, 9, 3), "Storage", -1),
]


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "a.duckdb"
    migrate(db)
    c = duckdb.connect(str(db))
    for d, svc, cost in ROWS:
        c.execute(f"INSERT INTO agg_daily ({COLS}) VALUES (?, ?, 'amortized', 'a1', 'GBP', ?, 'Usage', 'x', 'e', ?, ?, ?, 0, 0, 1, false)",
                  [d, d.replace(day=1), svc, cost, cost, cost])
    yield c
    c.close()


SEP = q.Filters("amortized", "GBP", start=date(2026, 9, 1), end=date(2026, 9, 30))


def test_series_top_n_others_and_gaps(con):
    r = q.analysis(con, SEP, "service", "day", n=2)
    assert r["periods"] == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]       # 2 Sep filled with zeros
    by = {s["key"]: s for s in r["series"]}
    assert list(by) == ["VM", "SQL", q.OTHER]
    assert by["VM"]["values"] == [D(10), D(0), D(12)] and by[q.OTHER]["values"] == [D(0), D(0), D(1)]   # 2 - 1
    assert r["totals"] == [D(14), D(0), D(13)] and r["total"] == D(27) and r["grouped"] == 2
    assert r["last_day"] == date(2026, 9, 3)


def test_month_grain_and_bad_input(con):
    r = q.analysis(con, q.Filters("amortized", "GBP"), "service", "month")
    assert r["periods"] == [date(2026, 8, 1), date(2026, 9, 1)] and r["totals"] == [D(5), D(27)]
    with pytest.raises(ValueError):
        q.analysis(con, SEP, "nope")
    with pytest.raises(ValueError):
        q.analysis(con, SEP, "service", "week")


def test_previous_range():
    assert q.previous_range(date(2026, 9, 1), date(2026, 9, 30)) == (date(2026, 8, 1), date(2026, 8, 31))
    assert q.previous_range(date(2026, 1, 1), date(2026, 2, 28)) == (date(2025, 11, 1), date(2025, 12, 31))
    assert q.previous_range(date(2026, 9, 10), date(2026, 9, 19)) == (date(2026, 8, 31), date(2026, 9, 9))


def test_budgets_and_views_are_saved_and_checked(tmp_path):
    assert prefs.save_budget(tmp_path, "all", "usd", "50000")[0] == {**prefs.budgets(tmp_path)[0], "amount": "50000", "currency": "USD"}
    prefs.save_budget(tmp_path, "100001", "USD", "30000")
    assert len(prefs.budgets(tmp_path)) == 2
    prefs.save_budget(tmp_path, "all", "USD", None)
    assert [b["scope"] for b in prefs.budgets(tmp_path)] == ["100001"]
    for bad in (("all", "US", "1"), ("../x", "USD", "1"), ("all", "USD", "-5"), ("all", "USD", "abc")):
        with pytest.raises(ValueError):
            prefs.save_budget(tmp_path, *bad)
    prefs.save_view(tmp_path, "VMs by sub", "?view=analysis&ag=subscription&services=Virtual%20Machines")
    assert prefs.views(tmp_path)[0]["query"] == "view=analysis&ag=subscription&services=Virtual%20Machines"
    with pytest.raises(ValueError):
        prefs.save_view(tmp_path, "bad", "view=x<script>")
    prefs.save_view(tmp_path, "VMs by sub", None)
    assert prefs.views(tmp_path) == []
