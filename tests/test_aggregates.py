"""Needs duckdb + pyarrow (first executed on the author's Windows PC)."""
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from azfinops import queries as q
from azfinops.aggregates import affected_months, rebuild
from azfinops.config import Settings
from azfinops.ingestion.importer import import_files
from azfinops.migrations.runner import migrate

FIX = Path(__file__).parent / "fixtures" / "ea_amortized"
MAIN, RESUB = FIX / "ea_amortized_sep.csv", FIX / "overlap_resubmission.csv"
S = Settings(app_tag_keys=("app",))      # the fixture's only tagged row uses key "app"
F = q.Filters("amortized", "GBP")


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "t.duckdb"
    migrate(p)
    import_files(p, [MAIN], "amortized", max_reject_ratio=1.0)
    rebuild(p, S)
    return p


@pytest.fixture
def con(db):
    c = duckdb.connect(str(db), read_only=True)
    yield c
    c.close()


def r2(x, n=2):
    return round(x, n)


def test_totals_reconcile_to_fixture(con):
    t = q.totals(con, F)
    assert (t["total"], t["usage"], t["rows"]) == (D("16.25"), D("15.45"), 9)
    assert t["by_charge_type"] == {"Usage": D("15.45"), "UnusedReservation": D("0.8")}


def test_actual_basis_is_empty_not_mixed(con):
    assert q.totals(con, q.Filters("actual", "GBP"))["total"] == 0
    assert q.totals(con, q.Filters("amortized", "USD"))["total"] == 0
    c = q.commitments(con, q.Filters("actual", "GBP"))
    assert c["available"] is False


def test_series_and_top_lists(con):
    assert [(s["period"], s["total"]) for s in q.series(con, F, "day")] == [(date(2026, 9, 2), D("16.25"))]
    assert [(s["period"], s["total"]) for s in q.series(con, F, "month")] == [(date(2026, 9, 1), D("16.25"))]
    svc = q.top(con, F, "service")
    assert [(r["key"], r["cost"]) for r in svc["rows"]] == [
        ("Virtual Machines", D("12.75")), ("Storage", D("2.5")), ("SaaS", D(1)), ("Bandwidth", D(0))]
    assert svc["groups"] == 4 and svc["other"] == 0
    app = q.top(con, F, "application")
    assert [(r["key"], r["cost"]) for r in app["rows"]] == [("(untagged)", D("13.75")), ("x", D("2.5"))]
    env = q.top(con, F, "environment")
    assert [r["key"] for r in env["rows"]] == ["(none)"]


def test_reservation_utilization_per_reservation_and_cost_weighted(con):
    # hand-derived from the fixture: R1 used 4 h / 1.60, unused 2 h / 0.80; R2 has one zero-hour unused row
    r = q.reservations(con, F)
    assert r["available"] and r["count"] == 2 and r["grain"] == "month"
    r1, r2 = r["rows"]
    assert (r1["commitment_id"], r1["used_hours"], r1["unused_hours"]) == ("R1", D(4), D(2))
    assert (r1["used_cost"], r1["unused_cost"]) == (D("1.6"), D("0.8"))
    assert r2["commitment_id"] == "R2" and r2["utilization"] is None
    assert round(r1["utilization"], 6) == round(D(4) / D(6), 6)
    assert r["without_used_rows"] == 0 and r["filters_not_applied"] == []
    u = q.commitments(con, F)["utilization"]
    assert (u["used_cost"], u["unused_cost"]) == (D("1.6"), D("0.8"))
    assert round(u["ratio_cost"], 6) == round(D("1.6") / D("2.4"), 6)
    # per reservation the cost ratio equals the hours ratio (constant rate per hour)
    assert round(u["ratio_cost"], 6) == round(r1["utilization"], 6)
    assert q.reservations(con, q.Filters("actual", "GBP"))["available"] is False
    ignored = q.reservations(con, q.Filters("amortized", "GBP", regions=("uksouth",)))
    assert ignored["filters_not_applied"] == ["regions"]


def test_commitments_detail_hand_derived_and_reconciles_to_headline(con):
    # Virtual Machines is the only category with benefit usage. By hand from the fixture:
    #   covered PayG = reservation 4 h x 0.80 (contract UnitPrice, PayGPrice is 0) + plan 5 h x 0.15 = 3.95
    #   on-demand PayG = 2 rows x (10 h x 0.50) = 10 (two rows without a price are excluded, not zero)
    #   benefit cost = 1.60 + 0.35 = 1.95 -> gross 2.00; unused 0.80 -> net 1.20; coverage 3.95 / 13.95
    d = q.commitments_detail(con, F)
    assert d["available"] and d["category_count"] == 1
    c = d["by_category"][0]
    assert c["category"] == "Virtual Machines" and c["eligible"] is True
    assert (c["used_cost"], c["savings_plan_cost"], c["unused_total"]) == (D("1.6"), D("0.35"), D("0.8"))
    assert round(c["covered"], 6) == D("3.95") and round(c["on_demand"], 6) == D(10)
    assert round(c["gross"], 6) == D("2.0") and round(c["net"], 6) == D("1.2")
    assert round(c["coverage"], 6) == round(D("3.95") / D("13.95"), 6)
    assert round(c["utilization"], 6) == round(D("1.6") / D("2.4"), 6)
    head = q.commitments(con, F)
    assert round(head["coverage"]["covered"], 9) == round(sum(r["covered"] for r in d["by_category"] if r["eligible"]), 9)
    assert round(head["coverage"]["eligible"], 9) == round(
        sum(r["covered"] + r["on_demand"] for r in d["by_category"] if r["eligible"]), 9)
    assert round(head["observed_discount"]["net"], 9) == round(sum(r["net"] for r in d["by_category"]), 9)
    m = d["by_month"]
    assert [x["month"] for x in m] == [date(2026, 9, 1)]
    assert round(m[0]["covered"], 9) == round(head["coverage"]["covered"], 9)
    assert d["spend"] == {"used_cost": D("1.6"), "savings_plan_cost": D("0.35"),
                          "unused_reservation": D("0.8"), "unused_savings_plan": D(0)}
    assert q.commitments_detail(con, q.Filters("actual", "GBP"))["available"] is False


def test_applications_coverage_adds_up_and_lists_gaps(con):
    a = q.applications(con, F)
    assert (a["total"], a["attributed"], a["unattributed"], a["untagged"]) == (D("16.25"), D("2.5"), D(0), D("13.75"))
    assert r2(a["attributed_share"], 6) == D("0.153846") and a["application_count"] == 1
    assert a["environment_none"] == D("16.25")
    g = a["gap_by_service"]
    assert g[0]["key"] == "Virtual Machines" and g[0]["untagged"] == D("12.75")
    assert sum(x["cost"] for x in g) + a["gap_other"] == a["untagged"] + a["unattributed"]
    assert q.applications(con, q.Filters("amortized", "GBP", services=("Storage",)))["untagged"] == 0
    assert q.applications(con, q.Filters("actual", "GBP"))["attributed_share"] is None
    one = q.applications(con, F, n=1)
    assert len(one["gap_by_service"]) == 1 and one["gap_groups"] >= 2
    assert one["gap_by_service"][0]["cost"] + one["gap_other"] == D("13.75")


def test_workload_families_cover_the_whole_total_and_pricing_is_hand_derived(con):
    wf = q.workload_families(con, F)          # the fixture has no AI, data-platform or database services
    assert wf["total"] == D("16.25") and wf["outside_families"] == D("16.25")
    assert all(g["total"] == 0 for g in wf["groups"]) and wf["months"] == [date(2026, 9, 1)]
    pr = q.pricing(con, F)
    vm = next(r for r in pr["by_service"] if r["key"] == "Virtual Machines")
    # PayG value of the priced Virtual Machines usage: 2 x (10 h x 0.50) + reservation 4 h x 0.80 contract
    # + savings plan 5 h x 0.15 = 13.95. The fixture's two blank-price rows are rejected at import (9 rows load, not 11),
    # so none reach the aggregate and missing_rows is 0 here
    assert vm["payg_equivalent"] == D("13.95") and vm["missing_rows"] == 0 and vm["contract_rows"] == 1
    assert vm["cost_with_payg"] <= vm["cost"] and round(vm["ratio_paid"], 9) == round(vm["cost_with_payg"] / D("13.95"), 9)
    assert pr["totals"]["cost"] == D("15.45")                    # Usage only; the unused reservation is excluded
    assert sum(r["cost"] for r in pr["by_service"]) == pr["totals"]["cost"] and pr["service_groups"] == 4
    assert sum(r["cost"] for r in pr["by_pricing_model"]) == pr["totals"]["cost"]
    assert pr["reference"]["price_benchmarks"] == 0
    assert q.pricing(con, q.Filters("actual", "GBP"))["totals"]["ratio_paid"] is None


def test_truncated_ranking_still_reconciles_to_total(con):
    r = q.top(con, F, "service", n=1)
    assert len(r["rows"]) == 1 and r["groups"] == 4
    assert r["rows"][0]["cost"] + r["other"] == r["total"] == D("16.25")


def test_filters_narrow_every_figure(con):
    assert q.totals(con, q.Filters("amortized", "GBP", services=("Storage",)))["total"] == D("2.5")
    assert q.totals(con, q.Filters("amortized", "GBP", applications=("x",)))["total"] == D("2.5")
    assert q.totals(con, q.Filters("amortized", "GBP", date(2026, 9, 3), date(2026, 9, 30)))["total"] == 0


def test_commitment_metrics_match_hand_computed_values(con):
    c = q.commitments(con, F)
    u, cv, d = c["utilization"], c["coverage"], c["observed_discount"]
    assert (u["used_hours"], u["unused_hours"]) == (D(4), D(2)) and r2(u["ratio"], 6) == D("0.666667")
    assert (r2(cv["covered"]), r2(cv["eligible"]), r2(cv["ratio"], 6)) == (D("3.95"), D("13.95"), D("0.283154"))
    assert cv["contract_basis_rows"] == 1 and cv["excluded_missing_price_rows"] == 0
    assert (r2(d["gross"]), r2(d["unused_cost"]), r2(d["net"])) == (D("2.00"), D("0.80"), D("1.20"))


def test_quality_flags_partial_month_and_attribution(con):
    ql = q.quality(con, F)
    assert ql["synthetic"] is False and ql["has_incomplete_month"] is True
    assert [m["complete"] for m in ql["months"]] == [False] and ql["months"][0]["rows"] == 9
    assert r2(ql["attributed_share"], 6) == D("0.153846")   # 2.5 / 16.25


def test_rebuild_is_idempotent(db):
    before = (duckdb.connect(str(db), read_only=True))
    a = before.execute("SELECT count(*), sum(cost) FROM agg_daily").fetchall()
    before.close()
    rebuild(db, S)
    rebuild(db, S, [date(2026, 9, 1)])
    c = duckdb.connect(str(db), read_only=True)
    assert c.execute("SELECT count(*), sum(cost) FROM agg_daily").fetchall() == a
    assert c.execute("SELECT count(*) FROM aggregate_freshness").fetchall()[0][0] == 2   # one per aggregate
    c.close()


def test_supersede_then_partial_rebuild_replaces_the_month(db):
    r = import_files(db, [RESUB], "amortized", supersede=True)
    c = duckdb.connect(str(db), read_only=True)
    months = affected_months(c, [r.batch_id, *r.superseded_batches])
    c.close()
    assert months == [date(2026, 9, 1)]
    rebuild(db, S, months)
    c = duckdb.connect(str(db), read_only=True)
    assert q.totals(c, F)["total"] == D(9)                 # superseded batch no longer in aggregates
    c.close()


def test_a_fully_covered_month_is_marked_complete(tmp_path):
    p = tmp_path / "t.duckdb"
    migrate(p)
    f = tmp_path / "full.csv"
    f.write_text("BillingAccountId,Date,Cost,BillingCurrency,ChargeType\r\n" +
                 "".join(f"A,09/{d:02d}/2026,1,GBP,Usage\r\n" for d in range(1, 31)))
    import_files(p, [f], "amortized")
    rebuild(p, S)
    c = duckdb.connect(str(p), read_only=True)
    ql = q.quality(c, F)
    c.close()
    assert ql["months"][0]["complete"] is True and ql["has_incomplete_month"] is False
    assert q.totals(duckdb.connect(str(p), read_only=True), F)["total"] == D(30)


@pytest.mark.parametrize("currency", ["GBP", "USD"])
def test_completeness_is_per_enrollment_and_currency(tmp_path, currency):
    p, export = tmp_path / "t.duckdb", tmp_path / "coverage.csv"
    migrate(p)
    export.write_text("BillingAccountId,Date,Cost,BillingCurrency,ChargeType\n" +
                      "".join(f"A,2026-09-{d:02},1,GBP,Usage\n" for d in range(1, 31)) +
                      f"B,2026-09-30,100,{currency},Usage\n")
    import_files(p, [export], "amortized")
    rebuild(p, S)
    with duckdb.connect(str(p), read_only=True) as con:
        complete = q.quality(con, q.Filters("amortized", "GBP", billing_account_ids=("A",)))
        partial = q.quality(con, q.Filters("amortized", currency, billing_account_ids=("B",)))
        combined = q.quality(con, q.Filters("amortized", "GBP", combined=True))
        assert not complete["has_incomplete_month"]
        assert partial["has_incomplete_month"] and partial["months"][0]["incomplete_accounts"] == ["B"]
        assert combined["has_incomplete_month"]
        assert con.execute("SELECT count(*) FROM aggregate_invalidations").fetchone()[0] == 0


def test_missing_entire_month_cannot_be_certified_by_another_enrollment(tmp_path):
    p, export = tmp_path / "t.duckdb", tmp_path / "coverage.csv"
    migrate(p)
    export.write_text("BillingAccountId,Date,Cost,BillingCurrency,ChargeType\n" +
                      "".join(f"A,2026-09-{d:02},1,GBP,Usage\n" for d in range(1, 31)) +
                      "B,2026-08-01,10,GBP,Usage\n")
    import_files(p, [export], "amortized")
    rebuild(p, S)
    with duckdb.connect(str(p), read_only=True) as con:
        f = q.Filters("amortized", "GBP", start=date(2026, 9, 1), end=date(2026, 9, 30))
        months = q.period_freshness(con, f)
        assert months[0]["complete"] is False and months[0]["incomplete_accounts"] == ["B"]


def test_outside_coverage_dates_are_flagged_without_expanding_millennia(db):
    with duckdb.connect(str(db), read_only=True) as con:
        months = q.period_freshness(
            con, q.Filters("amortized", "GBP", start=date(1, 1, 1), end=date(9999, 12, 31)))
    assert [m["month"] for m in months] == [date(1, 1, 1), date(2026, 9, 1), date(9999, 12, 1)]
    assert months[0]["complete"] is False and months[-1]["complete"] is False
