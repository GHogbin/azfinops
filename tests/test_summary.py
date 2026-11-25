"""The Overview's headline strip (hand-computed below)."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import queries as q
from azfinops.aggregates import _build_scope_freshness
from azfinops.migrations.runner import migrate

DCOLS = ("usage_date, usage_month, cost_kind, billing_account_id, billing_currency, meter_category, charge_type, "
         "subscription_id, application, environment, cost, payg_equivalent, cost_with_payg, payg_missing_rows, "
         "contract_basis_rows, row_count, is_synthetic")
RCOLS = ("usage_month, cost_kind, billing_currency, subscription_id, resource_id, meter_category, charge_type, "
         "application, environment, cost, row_count, is_synthetic")


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "s.duckdb"
    migrate(db)
    c = duckdb.connect(str(db))
    start = date(2026, 8, 1)
    for i in range(61):                         # 1 Aug to 30 Sep: VM 10 a day in August, 12 in September
        d = start + timedelta(days=i)
        vm = 10 if d.month == 8 else 12
        for svc, cost, sub in (("VM", vm, "s1"), ("SQL", 3, "s2"), ("Storage", 1, "s1"), ("Web", 1, "s3")):
            c.execute(f"INSERT INTO agg_daily ({DCOLS}) VALUES (?, ?, 'amortized', 'a1', 'GBP', ?, 'Usage', ?, 'x', 'e', ?, ?, ?, 0, 0, 2, false)",
                      [d, d.replace(day=1), svc, sub, cost, cost, cost])
    for m, rid in ((date(2026, 8, 1), "r1"), (date(2026, 9, 1), "r1"), (date(2026, 9, 1), "r2")):
        c.execute(f"INSERT INTO agg_resource_month ({RCOLS}) VALUES (?, 'amortized', 'GBP', 's1', ?, 'VM', 'Usage', 'x', 'e', 1, 1, false)", [m, rid])
    for month in (date(2026, 8, 1), date(2026, 9, 1)):
        _build_scope_freshness(c, month)
    yield c
    c.close()


def test_summary(con):
    r = q.summary(con, q.Filters("amortized", "GBP", start=date(2026, 9, 1), end=date(2026, 9, 30)))   # period ignored
    aug, sep = 31 * (10 + 5), 30 * (12 + 5)
    assert r["total"] == D(aug + sep) and (r["first_day"], r["last_day"]) == (date(2026, 8, 1), date(2026, 9, 30))
    assert r["days_with_data"] == 61 and r["billing_records"] == 61 * 4 * 2
    assert (r["subscriptions"], r["resources"]) == (3, 2)
    lm = r["latest_month"]
    assert (lm["month"], lm["cost"], lm["previous_cost"]) == (date(2026, 9, 1), D(sep), D(aug))
    assert lm["change"] == pytest.approx((sep - aug) / aug)
    t30, t60, t90 = r["trends"]
    assert t30["available"] and t30["per_day"] == D(17)              # last 30 days are all September
    assert t30["previous_per_day"] == D(15)                          # the 30 days before: 2-31 August
    assert t30["change"] == pytest.approx(2 / 15)
    assert not t60["available"] and t60["days_needed"] == 120 and not t90["available"]
    assert [x["service"] for x in r["top_services"]] == ["VM", "SQL", "Storage"]
    assert r["top3_share"] == pytest.approx((aug + sep - 61) / (aug + sep))


def test_no_data(con):
    assert not q.summary(con, q.Filters("amortized", "EUR"))["available"]


@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize("service", [None, "VM", "missing"])
def test_single_scan_rollup_equals_the_previous_three_scans(con, combined, service):
    from azfinops import combined as fx_view

    f = q.Filters("amortized", "GBP", combined=combined, services=(service,) if service else ())
    if combined:
        con.execute(f"INSERT INTO agg_daily ({DCOLS}) VALUES (DATE '2026-09-30', DATE '2026-09-01', "
                    "'amortized', 'u', 'USD', NULL, 'Credit', NULL, 'x', 'e', -1.0000625, 0, 0, 0, 0, 1, false)")
        fx_view.install(con, "GBP", [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.8"}])
    w, p = f.where()
    head = con.execute("SELECT sum(cost), sum(row_count), min(usage_date), max(usage_date), "
                       "count(DISTINCT subscription_id) FILTER (WHERE subscription_id IS NOT NULL) "
                       f"FROM agg_daily WHERE {w}", p).fetchall()[0]
    days = con.execute(f"SELECT usage_date, sum(cost) FROM agg_daily WHERE {w} GROUP BY 1 ORDER BY 1", p).fetchall()
    services = con.execute(f"SELECT meter_category, sum(cost) AS c FROM agg_daily WHERE {w} "
                           "GROUP BY 1 ORDER BY c DESC, 1 LIMIT 3", p).fetchall()
    assert q._summary_rollup(con, f) == (head, days, services)


def test_native_id_counts_still_exclude_currencies_without_a_rate(con):
    from azfinops import combined

    for currency, identifier in (("USD", "converted"), ("EUR", "excluded")):
        con.execute(f"INSERT INTO agg_daily ({DCOLS}) VALUES (DATE '2026-09-30', DATE '2026-09-01', "
                    "'amortized', 'foreign', ?, 'VM', 'Usage', ?, 'x', 'e', 10, 10, 10, 0, 0, 1, false)",
                    [currency, identifier])
        con.execute(f"INSERT INTO agg_resource_month ({RCOLS}) VALUES (DATE '2026-09-01', 'amortized', ?, ?, ?, "
                    "'VM', 'Usage', 'x', 'e', 10, 1, false)", [currency, identifier, identifier])
    combined.install(con, "GBP", [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.8"}])
    result = q.summary(con, q.Filters("amortized", "GBP", combined=True))
    assert result["subscriptions"] == 4 and result["resources"] == 3
