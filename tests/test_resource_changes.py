"""Resized and decommissioned resources between two months, with run-rate savings (hand-computed below)."""
from datetime import date
from decimal import Decimal as D

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import queries as q
from azfinops.migrations.runner import migrate

COLS = ("usage_month, cost_kind, billing_account_id, billing_currency, subscription_id, resource_group, resource_id, "
        "resource_name, meter_category, meter_name, charge_type, application, environment, cost, row_count, "
        "is_synthetic, first_date, last_date, active_days")
AUG, SEP = date(2026, 8, 1), date(2026, 9, 1)

# (month, resource, meter, cost, first day, last day, active days)
ROWS = [
    (AUG, "vm1", "D8s v5", 310, 1, 31, 31),   # downsized on 11 Sep: 10 per day -> 5 per day
    (SEP, "vm1", "D8s v5", 100, 1, 10, 10),
    (SEP, "vm1", "D4s v5", 100, 11, 30, 20),
    (AUG, "vm2", "E4s v5", 62, 1, 31, 31),    # decommissioned: no September usage, 2 per day
    (AUG, "vm3", "B2ms", 31, 1, 31, 31),      # stopped on 15 Sep: 1 per day
    (SEP, "vm3", "B2ms", 15, 1, 15, 15),
    (AUG, "vm4", "D2s v5", 31, 1, 31, 31),    # upsized on 1 Sep: 1 per day -> 3 per day
    (SEP, "vm4", "D4s v5", 90, 1, 30, 30),
    (AUG, "vm5", "D2s v5", 31, 1, 31, 31),    # unchanged
    (SEP, "vm5", "D2s v5", 30, 1, 30, 30),
    (AUG, "vm6", "D2s v5", 10, 1, 10, 10),    # stopped in August already: not a September change
    (SEP, "vm7", "D2s v5", 30, 1, 30, 30),    # new in September: not listed
]


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "r.duckdb"
    migrate(db)
    c = duckdb.connect(str(db))
    for m, rid, meter, cost, d0, d1, days in ROWS:
        c.execute(f"INSERT INTO agg_resource_month ({COLS}) VALUES (?, 'amortized', 'acct1', 'GBP', 'sub1', 'rg1', ?, ?, "
                  "'Virtual Machines', ?, 'Usage', 'a', 'e', ?, 1, false, ?, ?, ?)",
                  [m, rid, f"{rid}-name", meter, cost, m.replace(day=d0), m.replace(day=d1), days])
    for month, days in ((AUG, 31), (SEP, 30)):
        c.execute("INSERT INTO aggregate_scope_freshness VALUES "
                  "(?, 'amortized', 'acct1', 'GBP', ?, ?, ?, 1, true, now())",
                  [month, month, month.replace(day=days), days])
    yield c
    c.close()


def by_id(r):
    return {x["resource_id"]: x for x in r["rows"]}


def test_changes_are_found_and_valued_at_run_rate(con):
    r = q.resource_changes(con, q.Filters("amortized", "GBP", start=SEP, end=date(2026, 9, 30)))
    assert r["available"] and (r["month_from"], r["month_to"], r["days_to"]) == (AUG, SEP, 30)
    x = by_id(r)
    assert set(x) == {"vm1", "vm2", "vm3", "vm4"}
    assert (x["vm1"]["kind"], x["vm1"]["sku_from"], x["vm1"]["sku_to"], x["vm1"]["saving"]) == ("downsized", "D8s v5", "D4s v5", D(150))
    assert (x["vm2"]["kind"], x["vm2"]["sku_to"], x["vm2"]["saving"], x["vm2"]["cost_to"]) == ("decommissioned", None, D(60), D(0))
    assert (x["vm3"]["kind"], x["vm3"]["saving"], x["vm3"]["stopped_on"]) == ("decommissioned", D(30), date(2026, 9, 15))
    assert (x["vm4"]["kind"], x["vm4"]["saving"]) == ("upsized", D(-60))
    t = r["totals"]
    assert (t["decommissioned"]["count"], t["decommissioned"]["saving"]) == (2, D(90))
    assert (t["downsized"]["count"], t["upsized"]["count"]) == (1, 1)
    assert r["net_saving"] == D(180) and r["by_account"] == [{"account": "acct1", "saving": D(180)}]


def test_needs_two_months_and_rebuilt_aggregates(con):
    only_aug = q.resource_changes(con, q.Filters("amortized", "GBP", start=AUG, end=date(2026, 8, 31)))
    assert not only_aug["available"] and "Jul 2026" in only_aug["reason"]
    con.execute("UPDATE agg_resource_month SET active_days = NULL WHERE resource_id = 'vm5'")
    stale = q.resource_changes(con, q.Filters("amortized", "GBP"))
    assert not stale["available"] and "azfinops-aggregate" in stale["reason"]
