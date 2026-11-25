"""Savings-plan headroom against rows inserted by hand (needs duckdb). All expectations are worked out here.

Price sheet (Sep only, offer O1, GBP):  per-block prices, usage is per 1 unit.
  MA  Consumption 100 Hours 10.00 (0.10/h)   SavingsPlan P1Y 8.00 (0.08/h)   P3Y 6.00 (0.06/h)
  MB  Consumption 1 GB 1.00                  (no savings plan price: not eligible)
  MC  Consumption '1 /Month' 15              SavingsPlan P3Y '1 /Month' 20  (dearer than on-demand: saving floors at 0)
  MF  Consumption '1 Hour' 1.00 AND '10 Hours' 5.00   two prices for the same base unit: ambiguous
  MA  USD rows: Consumption 100 Hours 20.00
Usage (On Demand, Usage, amortized): Sep  MA 100 (VM)  MB 50 (Storage)  MC 30 (Backup)  MD 10 (VM, not on the sheet)
  ME 5 (VM, offer O2, sheet has only O1)  MF 7 (VM)       Aug  MA 40 (no price sheet for August)
Ignored: a Purchase row, a Reservation row, and a USD row (999) which belongs to the USD view.
  matched 100 + 50 + 30 = 180 ; unmatched 10 + 5 = 15 ; ambiguous 7 ; no sheet 40 ; total 242
  saving 1y: MA 100 x (1 - 0.08/0.10) = 20 ; saving 3y: MA 100 x (1 - 0.06/0.10) = 40, MC floors at 0
"""
from datetime import date
from decimal import Decimal as D

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import queries as q
from azfinops.migrations.runner import migrate
from azfinops.units import unit_parts, unit_sql

AUG, SEP = date(2026, 8, 1), date(2026, 9, 1)
F = q.Filters("amortized", "GBP")


def agg(month, meter, cat, unit, cost, offer="O1", charge="Usage", model="On Demand", ccy="GBP"):
    return (month, "amortized", ccy, "s1", "rg", "r1", cat, meter, f"name-{meter}", unit, charge, model, None, None,
            offer, "a1", "prod", cost, 1, 1, False)


def ps(meter, ptype, term, unit, price, offer="O1", ccy="GBP", month=SEP):
    return ("200001", month, None, meter, f"name-{meter}", None, None, None, None, None, None, None, None, "",
            unit, offer, ptype, term, ccy, price, None, None, None, "t.csv")


AGG = [agg(SEP, "MA", "Virtual Machines", "1 Hour", 100), agg(SEP, "MB", "Storage", "1 GB", 50),
       agg(SEP, "MC", "Backup", "1/Month", 30), agg(SEP, "MD", "Virtual Machines", "1 Hour", 10),
       agg(SEP, "ME", "Virtual Machines", "1 Hour", 5, offer="O2"), agg(SEP, "MF", "Virtual Machines", "1 Hour", 7),
       agg(AUG, "MA", "Virtual Machines", "1 Hour", 40),
       agg(SEP, "MA", "Virtual Machines", "1 Hour", 1000, charge="Purchase"),
       agg(SEP, "MA", "Virtual Machines", "1 Hour", 77, model="Reservation"),
       agg(SEP, "MA", "Virtual Machines", "1 Hour", 999, ccy="USD")]
SHEET = [ps("MA", "Consumption", "", "100 Hours", 10), ps("MA", "SavingsPlan", "P1Y", "100 Hours", 8),
         ps("MA", "SavingsPlan", "P3Y", "100 Hours", 6), ps("MB", "Consumption", "", "1 GB", 1),
         ps("MC", "Consumption", "", "1 /Month", 15), ps("MC", "SavingsPlan", "P3Y", "1 /Month", 20),
         ps("MF", "Consumption", "", "1 Hour", 1), ps("MF", "Consumption", "", "10 Hours", 5),
         ps("MA", "Consumption", "", "100 Hours", 20, ccy="USD")]


@pytest.fixture
def con(tmp_path):
    p = tmp_path / "s.duckdb"
    migrate(p)
    c = duckdb.connect(str(p))
    c.executemany("INSERT INTO agg_resource_month (usage_month, cost_kind, billing_currency, subscription_id, "
                  "resource_group, resource_id, meter_category, meter_id, meter_name, unit, charge_type, pricing_model, "
                  "commitment_id, reservation_name, offer_id, application, environment, cost, quantity, row_count, "
                  "is_synthetic) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", AGG)
    c.executemany("INSERT INTO price_sheet VALUES (" + ",".join("?" * 24) + ")", SHEET)
    yield c
    c.close()


def r(x, n=6):
    return round(x, n)


def test_totals_split_into_matched_unmatched_ambiguous_and_no_sheet(con):
    h = q.savings_headroom(con, F, latest_month=False)
    assert h["available"] and h["price_months"] == [SEP]
    t = h["totals"]
    assert (t["on_demand_cost"], t["matched_cost"], t["unmatched_cost"], t["ambiguous_cost"], t["no_sheet_cost"]) == (
        D(242), D(180), D(15), D(7), D(40))
    assert r(t["matched_share"], 6) == r(D(180) / D(242), 6)
    assert (r(t["eligible_cost_1y"]), r(t["eligible_cost_3y"])) == (D(100), D(130))      # has a plan price
    assert (r(t["cheaper_cost_1y"]), r(t["cheaper_cost_3y"])) == (D(100), D(100))        # MC's plan is dearer: not cheaper
    assert (r(t["saving_1y"]), r(t["saving_3y"])) == (D(20), D(40))        # MC floors at 0, never negative
    assert t["matched_cost"] + t["unmatched_cost"] + t["ambiguous_cost"] + t["no_sheet_cost"] == t["on_demand_cost"]


def test_breakdowns_reconcile_to_the_totals(con):
    h = q.savings_headroom(con, F, latest_month=False)
    assert [x["month"] for x in h["by_month"]] == [AUG, SEP]
    aug, sep = h["by_month"]
    assert (aug["on_demand_cost"], aug["no_sheet_cost"], aug["matched_cost"]) == (D(40), D(40), D(0))
    assert (sep["on_demand_cost"], sep["matched_cost"]) == (D(202), D(180))
    sv = {x["key"]: x for x in h["by_service"]}
    assert [x["key"] for x in h["by_service"]] == ["Virtual Machines", "Storage", "Backup"]   # saving, then cost
    assert sv["Virtual Machines"]["on_demand_cost"] == D(162) and r(sv["Virtual Machines"]["saving_3y"]) == D(40)
    assert sv["Backup"]["eligible_cost_3y"] == D(30) and sv["Backup"]["cheaper_cost_3y"] == 0 and sv["Backup"]["saving_3y"] == 0
    assert sum(x["on_demand_cost"] for x in h["by_service"]) == h["totals"]["on_demand_cost"]
    m = h["by_meter"][0]
    assert (m["meter_id"], m["name"], m["category"], m["on_demand_cost"]) == ("MA", "name-MA", "Virtual Machines", D(140))
    assert h["service_groups"] == 3 and h["meter_groups"] == 6


def test_truncation_and_filters(con):
    assert len(q.savings_headroom(con, F, n=1)["by_service"]) == 1
    s = q.savings_headroom(con, q.Filters("amortized", "GBP", services=("Storage",)))
    assert s["totals"]["on_demand_cost"] == D(50) and s["totals"]["matched_cost"] == D(50)
    assert q.savings_headroom(con, q.Filters("amortized", "GBP", regions=("uksouth",)))["filters_not_applied"] == ["regions"]


def test_currencies_stay_separate_and_a_missing_price_sheet_is_reported(con):
    usd = q.savings_headroom(con, q.Filters("amortized", "USD"))["totals"]
    assert (usd["on_demand_cost"], usd["matched_cost"], usd["saving_3y"]) == (D(999), D(999), D(0))
    none = q.savings_headroom(con, q.Filters("amortized", "EUR"))
    assert none["available"] is False and "EUR" in none["reason"] and none["totals"] is None


def test_sql_unit_rule_agrees_with_the_python_rule(con):
    units = ["1 Hour", "100 Hours", "1/Month", "1 /Month", "10K", "1000000", "10M", "1 GB/Month", "100 GB/Month",
             "1 TB", "1 /Day", "Each", ""]
    n, base = unit_sql("u")
    got = con.execute(f"SELECT u, {n}, {base} FROM (SELECT unnest(?) AS u)", [units]).fetchall()
    for u, blk, b in got:
        pn, pb = unit_parts(u)
        assert b == pb, u
        assert (None if blk is None else D(str(blk))) == (None if pn is None else pn), u


def test_by_default_only_the_latest_month_is_used(con):
    h = q.savings_headroom(con, F)
    assert h["month"] == SEP and [x["month"] for x in h["by_month"]] == [SEP]
    t = h["totals"]
    assert (t["on_demand_cost"], t["no_sheet_cost"], t["matched_cost"]) == (D(202), D(0), D(180))   # August's 40 left out
    aug = q.savings_headroom(con, q.Filters("amortized", "GBP", start=AUG, end=date(2026, 8, 31)))
    assert aug["month"] == AUG and aug["totals"]["on_demand_cost"] == D(40)          # latest month within the range


def test_daily_category_average_does_not_produce_a_purchase_recommendation(con):
    cols = ("usage_date, usage_month, cost_kind, billing_account_id, billing_currency, meter_category, charge_type, "
            "pricing_model, application, environment, cost, payg_equivalent, cost_with_payg, payg_missing_rows, "
            "contract_basis_rows, row_count, is_synthetic")
    for i in range(10):
        cost = 120 if i == 4 else 240
        con.execute(f"INSERT INTO agg_daily ({cols}) VALUES (?, ?, 'amortized', 'a1', 'GBP', 'Virtual Machines', "
                    "'Usage', 'On Demand', 'x', 'e', ?, ?, ?, 0, 0, 1, false)", [date(2026, 9, 1 + i), SEP, cost, cost, cost])
    rec = q.savings_headroom(con, F)["recommendation"]
    assert rec["available"] is False and rec["month"] == SEP
    assert rec["terms"] == {} and "hourly" in rec["reason"]


def test_no_daily_usage_means_no_recommendation(con):
    rec = q.savings_headroom(con, F)["recommendation"]
    assert rec["available"] is False and rec["terms"] == {}


def test_missing_meter_cost_is_reported_as_unmatched(con):
    row = agg(SEP, None, "Virtual Machines", "1 Hour", 50)
    con.execute("INSERT INTO agg_resource_month (usage_month, cost_kind, billing_currency, subscription_id, "
                "resource_group, resource_id, meter_category, meter_id, meter_name, unit, charge_type, pricing_model, "
                "commitment_id, reservation_name, offer_id, application, environment, cost, quantity, row_count, "
                "is_synthetic) VALUES (" + ",".join("?" * 21) + ")", row)
    h = q.savings_headroom(con, F)
    assert h["totals"]["on_demand_cost"] == D(252)
    assert h["totals"]["unmatched_cost"] == D(65)
    missing = next(m for m in h["by_meter"] if m["meter_id"] == "")
    assert missing["on_demand_cost"] == D(50) and missing["saving_3y"] == 0


def test_steady_category_spend_with_changing_eligibility_cannot_recommend_a_loss_making_plan(con):
    """GBP240/day: eligible MA for 15 days, ineligible MB for 15 days. A GBP3/h plan loses GBP360."""
    con.execute("DELETE FROM agg_resource_month")
    con.execute("DELETE FROM price_sheet")
    for meter in ("MA", "MB"):
        row = agg(SEP, meter, "Virtual Machines", "1 Hour", 3600)
        con.execute("INSERT INTO agg_resource_month (usage_month, cost_kind, billing_currency, subscription_id, "
                    "resource_group, resource_id, meter_category, meter_id, meter_name, unit, charge_type, pricing_model, "
                    "commitment_id, reservation_name, offer_id, application, environment, cost, quantity, row_count, "
                    "is_synthetic) VALUES (" + ",".join("?" * 21) + ")", row)
    con.executemany("INSERT INTO price_sheet VALUES (" + ",".join("?" * 24) + ")", [
        ps("MA", "Consumption", "", "1 Hour", 1),
        ps("MA", "SavingsPlan", "P3Y", "1 Hour", D("0.6")),
        ps("MB", "Consumption", "", "1 Hour", 1),
    ])
    cols = ("usage_date, usage_month, cost_kind, billing_account_id, billing_currency, meter_category, charge_type, "
            "pricing_model, application, environment, cost, payg_equivalent, cost_with_payg, payg_missing_rows, "
            "contract_basis_rows, row_count, is_synthetic")
    for day in range(1, 31):
        con.execute(f"INSERT INTO agg_daily ({cols}) VALUES (?, ?, 'amortized', 'a', 'GBP', 'Virtual Machines', "
                    "'Usage', 'On Demand', 'x', 'e', 240, 240, 240, 0, 0, 1, false)", [SEP.replace(day=day), SEP])
    h = q.savings_headroom(con, F)
    assert h["totals"]["on_demand_cost"] == D(7200)
    assert r(h["totals"]["saving_3y"]) == D(1440)
    assert h["recommendation"]["available"] is False and h["recommendation"]["terms"] == {}
    assert D(3) * 24 * 15 / D("0.6") - D(3) * 24 * 30 == D(-360)
