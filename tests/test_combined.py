"""Combined view: other currencies are converted by the saved monthly rate; months without a rate are left out."""
from datetime import date
from decimal import Decimal

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import combined, fx
from azfinops import queries as q
from azfinops.migrations.runner import migrate

COLS = ("usage_date, usage_month, cost_kind, billing_account_id, billing_currency, charge_type, application, "
        "environment, cost, payg_equivalent, cost_with_payg, payg_missing_rows, contract_basis_rows, row_count, is_synthetic")


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "c.duckdb"
    migrate(db)
    c = duckdb.connect(str(db))
    rows = [
        (date(2026, 9, 5), date(2026, 9, 1), "GBP", 100, 150),
        (date(2026, 9, 6), date(2026, 9, 1), "USD", 200, 300),
        (date(2026, 8, 5), date(2026, 8, 1), "USD", 50, 60),
    ]
    for d, m, ccy, cost, payg in rows:
        c.execute(f"INSERT INTO agg_daily ({COLS}) VALUES (?, ?, 'amortized', ?, ?, 'Usage', 'app', 'env', ?, ?, ?, 0, 0, 1, false)",
                  [d, m, "acctG" if ccy == "GBP" else "acctU", ccy, cost, payg, cost])
    yield c
    c.close()


RATES = [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.8"}]


def test_shared_rate_resolution_prefers_direct_and_earlier_nearest_month():
    assert combined.resolve is fx.resolve
    assert combined.rates_to is fx.rates_to
    rates = [
        {"month": "2026-01", "from": "USD", "to": "GBP", "rate": "0.5"},
        {"month": "2026-01", "from": "GBP", "to": "USD", "rate": "1.5"},
        {"month": "2026-03", "from": "GBP", "to": "USD", "rate": "1.7"},
    ]
    result = fx.resolve("USD", rates, [("GBP", "2026-01"), ("GBP", "2026-02"), ("EUR", "2026-02"), ("USD", "2026-02")])
    assert result["GBP", "2026-01"] == {"rate": Decimal("1.5"), "rate_month": "2026-01", "exact": True}
    assert result["GBP", "2026-02"] == {"rate": Decimal("1.5"), "rate_month": "2026-01", "exact": False}
    assert ("EUR", "2026-02") not in result
    assert ("USD", "2026-02") not in result


def test_converts_by_month_and_carries_the_nearest_rate_to_months_without_one(con):
    f = q.Filters("amortized", "GBP", combined=True)
    combined.install(con, "GBP", RATES)
    t = q.totals(con, f)
    assert t["total"] == Decimal(300)          # 100 + 200 * 0.8 + 50 * 0.8 (August uses September's saved rate)
    assert t["rows"] == 3
    pe = con.execute("SELECT sum(payg_equivalent) FROM agg_daily").fetchall()[0][0]
    assert pe == Decimal(438)                  # 150 + 300 * 0.8 + 60 * 0.8
    assert {r[0] for r in con.execute("SELECT DISTINCT billing_currency FROM agg_daily").fetchall()} == {"GBP"}


def test_native_currency_is_untouched_without_combined(con):
    assert q.totals(con, q.Filters("amortized", "USD"))["total"] == Decimal(250)
    assert q.totals(con, q.Filters("amortized", "GBP"))["total"] == Decimal(100)


def test_summary_reports_carried_and_missing_rates(con):
    s = combined.summary(con, "amortized", "GBP", RATES)
    assert s["missing"] == [] and s["carried"] == [("2026-08", "USD", "2026-09")]
    by = {(r["month"], r["currency"]): r for r in s["rows"]}
    assert by[("2026-09", "USD")]["converted"] == Decimal("160.0") and by[("2026-09", "USD")]["status"] == "converted"
    assert by[("2026-09", "GBP")]["status"] == "target"
    assert by[("2026-08", "USD")]["status"] == "carried" and by[("2026-08", "USD")]["converted"] == Decimal("40.0")
    none = combined.summary(con, "amortized", "GBP", [])
    assert none["missing"] == [("2026-08", "USD"), ("2026-09", "USD")]


def test_a_currency_with_no_saved_rate_is_left_out(con):
    combined.install(con, "GBP", [])
    assert q.totals(con, q.Filters("amortized", "GBP", combined=True))["total"] == Decimal(100)


def test_headroom_is_not_available_when_combined(con):
    r = q.savings_headroom(con, q.Filters("amortized", "GBP", combined=True))
    assert r["available"] is False and "combined" in r["reason"]


def test_bad_target_is_rejected(con):
    with pytest.raises(ValueError):
        combined.install(con, "GBP'; DROP TABLE agg_daily; --", [])
    assert fx  # module imports cleanly


def test_usd_target_uses_the_inverted_gbp_rate(con):
    combined.install(con, "USD", RATES)           # saved as USD->GBP 0.8; GBP->USD is 1 / 0.8 = 1.25
    t = q.totals(con, q.Filters("amortized", "USD", combined=True))
    assert t["total"] == Decimal(375)          # 200 + 50 native USD, plus 100 GBP * 1.25
    s = combined.summary(con, "amortized", "USD", RATES)
    assert s["target"] == "USD"


def test_by_account_shows_each_enrollment_natively_and_converted(con):
    combined.install(con, "GBP", RATES)
    rows = {r["account"]: r for r in q.by_account(con, q.Filters("amortized", "GBP", combined=True))}
    u, g = rows["acctU"], rows["acctG"]
    assert (u["currency"], u["cost"], u["converted"], u["partial"]) == ("USD", Decimal(250), Decimal(200), False)
    assert (g["currency"], g["cost"], g["converted"], g["partial"]) == ("GBP", Decimal(100), Decimal(100), False)
    assert abs(u["share"] + g["share"] - 1.0) < 1e-9 and abs(g["share"] - 100 / 300) < 1e-9


def test_top_rows_carry_a_split_by_enrollment(con):
    combined.install(con, "GBP", RATES)
    t = q.top(con, q.Filters("amortized", "GBP", combined=True), "application")
    row = t["rows"][0]
    assert row["cost"] == Decimal(300)
    assert {x["account"]: x["cost"] for x in row["split"]} == {"acctG": Decimal(100), "acctU": Decimal(200)}
    assert all(x["cost"] == 0 for x in t["other_split"])


def test_by_account_carries_the_card_figures_per_enrollment(con):
    combined.install(con, "GBP", RATES)
    rows = {r["account"]: r for r in q.by_account(con, q.Filters("amortized", "GBP", combined=True))}
    assert rows["acctU"]["usage"] == Decimal(200) and rows["acctU"]["metrics_currency"] == "GBP"
    assert rows["acctG"]["usage"] == Decimal(100)
    assert rows["acctG"]["attributed_share"] == 1


def test_series_by_account_adds_up_to_the_series_in_the_combined_view(con):
    combined.install(con, "GBP", RATES)
    f = q.Filters("amortized", "GBP", combined=True)
    for grain in ("day", "month"):
        total = {r["period"]: r["total"] for r in q.series(con, f, grain)}
        parts: dict = {}
        for r in q.series_by_account(con, f, grain):
            parts[r["period"]] = parts.get(r["period"], Decimal(0)) + r["total"]
        assert parts == total
    months = {(r["period"], r["account"]) for r in q.series_by_account(con, f, "month")}
    assert months == {(date(2026, 9, 1), "acctG"), (date(2026, 9, 1), "acctU"), (date(2026, 8, 1), "acctU")}


@pytest.mark.parametrize("cost", ["1.0000625", "-1.0000625", "123456789012345.678901234567890123"])
def test_conversion_is_decimal_exact_and_matches_summary(con, cost):
    from decimal import ROUND_HALF_EVEN, localcontext

    con.execute("DELETE FROM agg_daily")
    con.execute(f"INSERT INTO agg_daily ({COLS}) VALUES (DATE '2026-09-01', DATE '2026-09-01', "
                "'amortized', 'a', 'USD', 'Usage', 'x', 'e', ?, ?, ?, 0, 0, 1, false)",
                [Decimal(cost)] * 3)
    with localcontext() as ctx:
        ctx.prec = 80
        expected = (Decimal(cost) * Decimal("0.8")).quantize(Decimal("1e-18"), rounding=ROUND_HALF_EVEN)
    details = combined.summary(con, "amortized", "GBP", RATES)
    combined.install(con, "GBP", RATES)
    assert q.totals(con, q.Filters("amortized", "GBP", combined=True))["total"] == expected
    assert details["rows"][0]["converted"] == expected


@pytest.mark.parametrize("cost,rate,expected", [
    ("0.000000001", "0.0000000005", "0"),
    ("0.000000003", "0.0000000005", "0.000000000000000002"),
    ("-0.000000003", "0.0000000005", "-0.000000000000000002"),
    ("9999999999999999999.99", "1.25", "12499999999999999999.9875"),
])
def test_conversion_rounds_fractional_ties_to_even_without_intermediate_overflow(con, cost, rate, expected):
    combined.install(con, "GBP", [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": rate}])
    got = con.execute("SELECT azfinops_fx_money(CAST(? AS DECIMAL(38,18)), CAST(? AS DECIMAL(24,18)))",
                      [cost, rate]).fetchone()[0]
    assert got == Decimal(expected)


def test_rates_with_unrepresentable_precision_are_rejected(tmp_path):
    with pytest.raises(fx.FxError, match="18 decimal"):
        fx.save(tmp_path, "2026-09", "USD", "GBP", "0.8000000000000000001", "manual", "test")


@pytest.mark.parametrize("cache_daily_money", [False, True])
def test_scoped_rate_setup_preserves_previous_month_and_all_dates_queries(con, cache_daily_money):
    from dataclasses import replace

    from azfinops.aggregates import _build_scope_freshness

    for month in (date(2026, 8, 1), date(2026, 9, 1)):
        _build_scope_freshness(con, month)
    f = q.Filters("amortized", "GBP", start=date(2026, 9, 1), end=date(2026, 9, 30), combined=True)
    combined.install(con, "GBP", RATES, f, cache_daily_money=cache_daily_money)
    assert con.execute("SELECT count(*) FROM fx_daily_money").fetchone()[0] == int(cache_daily_money)
    assert q.totals(con, f)["total"] == Decimal(260)
    assert q.totals(con, replace(f, start=None, end=None))["total"] == Decimal(300)
    assert q.totals(con, replace(f, start=date(2026, 8, 1), end=date(2026, 8, 31)))["total"] == Decimal(40)
    details = combined.summary(con, "amortized", "GBP", RATES, f)
    assert sum(r["converted"] for r in details["rows"]) == Decimal(300)


def test_conversion_cache_respects_all_filters_without_excluding_cache_misses(con):
    from dataclasses import replace

    from azfinops.aggregates import _build_scope_freshness

    for month in (date(2026, 8, 1), date(2026, 9, 1)):
        _build_scope_freshness(con, month)
    con.execute("UPDATE agg_daily SET application = 'excluded' WHERE usage_date = DATE '2026-09-06'")
    f = q.Filters("amortized", "GBP", start=date(2026, 9, 1), end=date(2026, 9, 30),
                  applications=("app",), combined=True)
    combined.install(con, "GBP", RATES, f)
    assert con.execute("SELECT count(*) FROM fx_daily_money").fetchone()[0] == 0
    assert q.totals(con, f)["total"] == Decimal(100)
    assert q.totals(con, replace(f, applications=()))["total"] == Decimal(260)


def test_lazy_conversion_preserves_per_row_rounding_and_charge_overrides(con):
    from azfinops.aggregates import _build_scope_freshness

    con.execute("DELETE FROM agg_daily")
    for charge_type, cost in (("Usage", "0.000000000000000003"), ("Usage", "0.000000000000000003"),
                              ("Usage", "-0.000000000000000001"), ("UnusedReservation", "1.0000625")):
        con.execute(f"INSERT INTO agg_daily ({COLS}) VALUES (DATE '2026-09-01', DATE '2026-09-01', "
                    "'amortized', 'u', 'USD', ?, 'app', 'env', ?, ?, ?, 0, 0, 1, false)",
                    [charge_type, *[Decimal(cost)] * 3])
    _build_scope_freshness(con, date(2026, 9, 1))
    f = q.Filters("amortized", "GBP", start=date(2026, 9, 1), end=date(2026, 9, 30),
                  charge_types=("UnusedReservation",), combined=True)
    rates = [{"month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.5"}]
    combined.install(con, "GBP", rates, f, cache_daily_money=False)
    assert con.execute("SELECT count(*) FROM fx_daily_money").fetchone()[0] == 0
    lazy = q.overview(con, f, tops=("application",))
    assert lazy["totals"]["total"] == Decimal("0.50003125")
    assert lazy["pay_models"]["overall"] == [{"model": None, "cost": Decimal("0.000000000000000004")}]
    combined.install(con, "GBP", rates, f)
    assert lazy == q.overview(con, f, tops=("application",))
