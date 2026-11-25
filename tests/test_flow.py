"""Cost flow for the Sankey: top values per level, Other, and credits left out of the drawing."""
from datetime import date
from decimal import Decimal as D

import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops import queries as q
from azfinops.migrations.runner import migrate

COLS = ("usage_date, usage_month, cost_kind, billing_account_id, billing_currency, meter_category, charge_type, "
        "application, environment, cost, payg_equivalent, cost_with_payg, payg_missing_rows, contract_basis_rows, "
        "row_count, is_synthetic")

# (enrollment, service, application, cost)
ROWS = [("A", "VM", "app1", 100), ("A", "VM", "app2", 50), ("A", "SQL", "app1", 40), ("B", "VM", "app3", 30),
        ("B", "Storage", "app3", 5), ("B", "Web", "app4", 3), ("A", "VM", "app1", -20), ("B", "Web", "app4", -10)]


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "f.duckdb"
    migrate(db)
    c = duckdb.connect(str(db))
    for i, (a, svc, app, cost) in enumerate(ROWS):
        c.execute(f"INSERT INTO agg_daily ({COLS}) VALUES (?, ?, 'amortized', ?, 'GBP', ?, 'Usage', ?, 'e', ?, ?, ?, 0, 0, 1, false)",
                  [date(2026, 9, 1 + i), date(2026, 9, 1), a, svc, app, cost, cost, cost])
    yield c
    c.close()


F = q.Filters("amortized", "GBP")


def test_paths_with_top_values_and_other(con):
    r = q.flow(con, F, ("billing_account", "service", "application"), n=2)
    paths = {tuple(p["keys"]): p["value"] for p in r["paths"]}
    # services by net cost: VM 160, SQL 40, then Storage 5, Web -7 -> Other; applications: app1 120, app2 50 -> rest Other
    assert paths == {("A", "VM", "app1"): D(80), ("A", "VM", "app2"): D(50), ("A", "SQL", "app1"): D(40),
                     ("B", "VM", q.OTHER): D(30)}
    assert paths[("A", "VM", "app1")] == D(80) and paths[("B", "VM", q.OTHER)] == D(30)
    assert ("B", q.OTHER, q.OTHER) not in paths            # 5 + 3 - 10 = -2: a credit, not drawn
    assert r["total"] == D(198) and r["credits"] == D(-2) and r["drawn"] == D(200)
    assert sum(paths.values()) == r["drawn"]
    assert r["grouped"] == [0, 2, 2]


def test_levels_are_checked(con):
    for bad in (("service",), ("service", "service"), ("service", "nope"), ("a", "b", "c", "d")):
        with pytest.raises(ValueError):
            q.flow(con, F, bad)


def test_two_levels_and_no_cost(con):
    r = q.flow(con, F, ("billing_account", "service"), n=5)
    assert {tuple(p["keys"]) for p in r["paths"]} == {("A", "VM"), ("A", "SQL"), ("B", "VM"), ("B", "Storage")}
    assert r["credits"] == D(-7)                            # B -> Web nets to 3 - 10
    assert not q.flow(con, q.Filters("amortized", "EUR"))["available"]
