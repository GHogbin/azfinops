"""The per-enrollment card figures come from one grouped pass; they must equal running totals, commitments and
quality once per enrollment (the old way), in both the native and the Combined view."""
from dataclasses import replace
from datetime import date

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import combined
from azfinops import queries as q
from azfinops.migrations.runner import migrate

COLS = ("usage_date, usage_month, cost_kind, billing_account_id, billing_currency, meter_category, charge_type, "
        "pricing_model, publisher_type, application, environment, cost, payg_equivalent, cost_with_payg, "
        "payg_missing_rows, contract_basis_rows, quantity_hours, row_count, is_synthetic")

# (account, currency, category, charge type, pricing model, application, cost, payg equivalent, hours)
ROWS = [
    ("acctG", "GBP", "VM", "Usage", "Reservation", "app1", 80, 120, 10),
    ("acctG", "GBP", "VM", "Usage", "On Demand", "(untagged)", 50, 50, 0),
    ("acctG", "GBP", "VM", "UnusedReservation", "Reservation", "(unattributed)", 20, 0, 4),
    ("acctG", "GBP", "Storage", "Usage", "On Demand", "app1", 30, 30, 0),
    ("acctU", "USD", "SQL", "Usage", "SavingsPlan", "app2", 200, 260, 0),
    ("acctU", "USD", "SQL", "Usage", "On Demand", "app2", 100, 100, 0),
    ("acctU", "USD", "SQL", "UnusedSavingsPlan", "SavingsPlan", "(untagged)", 15, 0, 0),
    ("acctU", "USD", "Web", "Usage", "On Demand", "(untagged)", -5, -5, 0),
]


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "a.duckdb"
    migrate(db)
    c = duckdb.connect(str(db))
    for i, (a, ccy, cat, ct, pm, app, cost, pe, hrs) in enumerate(ROWS):
        c.execute(f"INSERT INTO agg_daily ({COLS}) VALUES (?, ?, 'amortized', ?, ?, ?, ?, ?, NULL, ?, 'e', ?, ?, ?, 0, 0, ?, 1, false)",
                  [date(2026, 9, 1 + i), date(2026, 9, 1), a, ccy, cat, ct, pm, app, cost, pe, cost, hrs])
    yield c
    c.close()


def old_way(con, f, acct, ccy):
    g = replace(f, billing_account_ids=(acct,)) if f.combined else replace(f, billing_account_ids=(acct,), currency=ccy)
    t, com = q.totals(con, g), q.commitments(con, g)
    od, ut, cv = com["observed_discount"], com["utilization"], com["coverage"]
    return {"total_metric": t["total"], "usage": t["usage"], "attributed_share": q.quality(con, g)["attributed_share"],
            "unused": od["unused_cost"], "discount_net": od["net"], "utilization": ut["ratio_cost"], "coverage": cv["ratio"]}


@pytest.mark.parametrize("view", ["native", "combined"])
def test_grouped_metrics_match_the_old_per_enrollment_calls(con, view):
    if view == "combined":
        combined.install(con, "GBP", [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.8"}])
    f = q.Filters("amortized", "GBP", combined=(view == "combined"))
    rows = q.by_account(con, f)
    assert {r["account"] for r in rows} == {"acctG", "acctU"}
    for r in rows:
        want = old_way(con, f, r["account"], r["currency"])
        got = {k: r[k] for k in want}
        assert got == want, r["account"]


def test_actual_basis_has_no_commitment_figures(con):
    con.execute("UPDATE agg_daily SET cost_kind = 'actual'")
    for r in q.by_account(con, q.Filters("actual", "GBP")):
        assert r["unused"] is None and r["coverage"] is None and r["usage"] is not None


def test_pay_models_replace_the_charge_type_filter_with_usage(con):
    pm = q.pay_models(con, q.Filters("amortized", "GBP", charge_types=("UnusedReservation",)))
    assert {(x["model"], int(x["cost"])) for x in pm["overall"]} == {("Reservation", 80), ("On Demand", 80)}
    assert {(x["account"], x["model"]) for x in pm["by_account"]} == {("acctG", "Reservation"), ("acctG", "On Demand")}


@pytest.mark.parametrize("combined_view", [False, True])
def test_overview_rollups_match_the_previous_individual_queries(con, combined_view):
    if combined_view:
        combined.install(con, "GBP", [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.8"}])
    f = q.Filters("amortized", "GBP", combined=combined_view)
    expected = {
        "daily": q.series(con, f, "day"),
        "monthly": q.series(con, f, "month"),
        "daily_by_account": q.series_by_account(con, f, "day"),
        "monthly_by_account": q.series_by_account(con, f, "month"),
        "by_account_month": q.by_account_month(con, f),
    }
    result = q.overview(con, f, tops=())
    assert {key: result[key] for key in expected} == expected


@pytest.mark.parametrize("combined_view", [False, True])
def test_compact_coverage_matches_raw_group_inputs(con, combined_view):
    if combined_view:
        combined.install(con, "GBP", [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.8"}])
    f = q.Filters("amortized", "GBP", combined=combined_view)
    w, p = f.where()
    covered, uncovered, benefit = con.execute(f"""
        WITH s AS (SELECT * FROM agg_daily WHERE {w}),
        cats AS (SELECT DISTINCT meter_category FROM s
                 WHERE charge_type = 'Usage' AND pricing_model IN ('Reservation', 'SavingsPlan'))
        SELECT sum(payg_equivalent) FILTER (WHERE pricing_model IN ('Reservation', 'SavingsPlan')),
               sum(payg_equivalent) FILTER (WHERE pricing_model = 'On Demand'),
               sum(cost_with_payg) FILTER (WHERE pricing_model IN ('Reservation', 'SavingsPlan'))
        FROM s WHERE charge_type = 'Usage' AND coalesce(publisher_type, '') <> 'Marketplace'
          AND meter_category IN (SELECT meter_category FROM cats)""", p).fetchone()
    result = q.commitments(con, f)
    assert result["coverage"]["covered"] == covered
    assert result["coverage"]["eligible"] == covered + uncovered
    assert result["observed_discount"]["benefit_cost"] == benefit


@pytest.mark.parametrize("combined_view", [False, True])
def test_compact_overview_restores_source_and_preserves_charge_override(con, combined_view):
    if combined_view:
        combined.install(con, "GBP", [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.8"}])
    f = q.Filters("amortized", "GBP", combined=combined_view, charge_types=("UnusedReservation",))
    before = con.execute("SELECT * FROM agg_daily ORDER BY ALL").fetchall()
    expected = {
        "totals": q.totals(con, f), "by_account": q.by_account(con, f),
        "pay_models": q.pay_models(con, f), "quality": q.quality(con, f),
        "commitments": q.commitments(con, f),
    }
    result = q.overview(con, f, tops=())
    assert {key: result[key] for key in expected} == expected
    assert con.execute("SELECT * FROM agg_daily ORDER BY ALL").fetchall() == before


def test_compact_overview_keeps_requested_application_names_and_active_filters(con):
    f = q.Filters("amortized", "GBP", applications=("app1",))
    before = q.top(con, f, "application")
    result = q.overview(con, f, tops=("application",))
    assert result["top"]["application"] == before
    assert result["totals"]["total"] == q.totals(con, f)["total"]


def test_compact_overview_restores_the_source_view_when_a_card_fails(con, monkeypatch):
    combined.install(con, "GBP", [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.8"}])
    before = con.execute("SELECT * FROM agg_daily ORDER BY ALL").fetchall()

    def fail(*args, **kwargs):
        raise RuntimeError("injected card failure")

    monkeypatch.setattr(q, "_overview", fail)
    with pytest.raises(RuntimeError, match="injected card failure"):
        q.overview(con, q.Filters("amortized", "GBP", combined=True), tops=())
    assert con.execute("SELECT * FROM agg_daily ORDER BY ALL").fetchall() == before
