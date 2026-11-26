"""API contract tests against the invented EA fixture (needs duckdb, pyarrow, fastapi, httpx).

Expected numbers are hand-derived from tests/fixtures/ea_amortized/ea_amortized_sep.csv (see docs/fixtures.md):
total 16.25, usage 15.45; Virtual Machines 12.75, Storage 2.5, SaaS 1, Bandwidth 0.
"""
import threading
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from azfinops.aggregates import rebuild
from azfinops.api import db as dbmod
from azfinops.api.app import create_app
from azfinops.api.db import Database, Overloaded, Paused
from azfinops.config import Settings
from azfinops.dimensions import harvest_and_store
from azfinops.ingestion.importer import import_files
from azfinops.migrations.runner import migrate

MAIN = Path(__file__).parent / "fixtures" / "ea_amortized" / "ea_amortized_sep.csv"
BASE = {"cost_kind": "amortized", "currency": "GBP"}


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("api") / "t.duckdb"
    s = Settings(data_dir=p.parent, app_tag_keys=("app",))
    migrate(p)
    import_files(p, [MAIN], "amortized", max_reject_ratio=1.0)
    rebuild(p, s)
    harvest_and_store(p, [MAIN])
    return p


@pytest.fixture
def client(db_path, tmp_path):
    app = create_app(Settings(data_dir=db_path.parent), db_path, allowed_hosts=("testserver",),
                     web_dir=tmp_path / "no-web")
    with TestClient(app) as c:
        yield c


def get(client, path, **params):
    return client.get(path, params={**BASE, **params})


def test_health_and_timing_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    st = r.headers["server-timing"]
    assert "total;dur=" in st and "db;dur=" in st
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"


def test_overview_reconciles_and_money_is_exact_strings(client):
    r = get(client, "/api/overview")
    assert r.status_code == 200
    o = r.json()
    assert o["totals"]["total"] == "16.2500" and o["totals"]["usage"] == "15.4500"
    assert o["totals"]["by_charge_type"] == {"UnusedReservation": "0.8000", "Usage": "15.4500"}
    assert o["totals"]["rows"] == 9
    sub = o["top"]["subscription"]
    assert sub["rows"][0]["label"] == "invented-sub" and sub["rows"][0]["cost"] == "16.2500"
    assert sub["other"] == "0.0000" and sub["groups"] == 1
    assert [p["total"] for p in o["daily"]] == ["16.2500"]
    u = o["commitments"]["utilization"]
    assert u["ratio_cost"] == pytest.approx(1.6 / 2.4, abs=1e-6)
    assert o["commitments"]["available"] is True
    assert o["quality"]["synthetic"] is False


@pytest.mark.parametrize("path", ["/api/overview", "/api/overview/summary", "/api/cost/movers"])
def test_overview_requests_do_not_preconvert_the_entire_selected_period(client, monkeypatch, path):
    from azfinops import combined, fx

    monkeypatch.setattr(fx, "load", lambda _data_dir: [
        {"month": "2026-09", "from": "GBP", "to": "USD", "rate": "0.8"}])
    install = combined.install
    cached_rows = []

    def observe(con, *args, **kwargs):
        install(con, *args, **kwargs)
        cached_rows.append(con.execute("SELECT count(*) FROM fx_daily_money").fetchone()[0])

    monkeypatch.setattr(combined, "install", observe)
    response = get(client, path, currency="USD", combined="true", start="2026-09-01", end="2026-09-30")
    assert response.status_code == 200, response.text
    assert cached_rows == [0]
    if path == "/api/overview":
        assert response.json()["totals"]["total"] == "13.0000"
    elif path == "/api/overview/summary":
        assert response.json()["total"] == "13.0000"


def test_actual_basis_is_empty_and_commitments_unavailable(client):
    o = client.get("/api/overview", params={"cost_kind": "actual", "currency": "GBP"}).json()
    assert o["totals"]["total"] == "0.0000"
    assert o["commitments"]["available"] is False and o["commitments"]["reason"]
    o2 = get(client, "/api/overview", currency="usd").json()      # case-insensitive code, no data
    assert o2["totals"]["total"] == "0.0000"


def test_explore_paging_reconciles_to_total(client):
    p1 = get(client, "/api/cost/explore", group_by="service", page_size=2).json()
    p2 = get(client, "/api/cost/explore", group_by="service", page_size=2, page=2).json()
    assert (p1["groups"], p1["pages"], p1["total"]) == (4, 2, "16.2500")
    assert [r["keys"][0] for r in p1["rows"]] == ["Virtual Machines", "Storage"]
    assert [r["keys"][0] for r in p2["rows"]] == ["SaaS", "Bandwidth"]
    from decimal import Decimal
    assert sum(Decimal(r["cost"]) for r in p1["rows"] + p2["rows"]) == Decimal(p1["total"])
    assert get(client, "/api/cost/explore", group_by="service", page_size=2, page=9).json()["rows"] == []


def test_explore_two_dimensions_sort_and_labels(client):
    r = get(client, "/api/cost/explore", group_by=["service", "charge_type"]).json()
    assert r["groups"] == 5
    k = get(client, "/api/cost/explore", group_by="service", sort="key", order="asc").json()
    assert [x["keys"][0] for x in k["rows"]] == ["Bandwidth", "SaaS", "Storage", "Virtual Machines"]
    s = get(client, "/api/cost/explore", group_by="subscription").json()
    assert s["rows"][0]["labels"] == ["invented-sub"]


@pytest.mark.parametrize("path,params", [
    ("/api/overview", {"cost_kind": "mixed", "currency": "GBP"}),
    ("/api/overview", {"cost_kind": "amortized", "currency": "GB"}),
    ("/api/overview", {"cost_kind": "amortized"}),
    ("/api/overview", {**BASE, "start": "2026-09-30", "end": "2026-09-01"}),
    ("/api/overview", {**BASE, "start": "not-a-date"}),
    ("/api/overview", {**BASE, "service": "x" * 201}),
    ("/api/cost/explore", {**BASE, "group_by": "nonsense"}),
    ("/api/cost/explore", BASE),
    ("/api/cost/explore", {**BASE, "group_by": ["service", "service"]}),
    ("/api/cost/explore", {**BASE, "group_by": ["service", "region", "environment"]}),
    ("/api/cost/explore", {**BASE, "group_by": "service", "page_size": 1000}),
    ("/api/cost/explore", {**BASE, "group_by": "service", "sort": "drop table"}),
    ("/api/dimensions/not_a_dimension", BASE),
])
def test_invalid_requests_are_rejected_not_executed(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 422, r.text


def test_hostile_filter_values_are_data_not_sql(client):
    evil = "'; DROP TABLE agg_daily; --"
    r = get(client, "/api/overview", service=evil)
    assert r.status_code == 200 and r.json()["totals"]["total"] == "0.0000"
    assert get(client, "/api/overview").json()["totals"]["total"] == "16.2500"      # table still there
    assert get(client, "/api/dimensions/service", search="%").json()["values"] == []   # LIKE wildcard escaped
    assert get(client, "/api/dimensions/service", search="'; --").json()["values"] == []


def test_dimension_values_ignore_own_selection_and_search_subscription_names(client):
    r = get(client, "/api/dimensions/service", service="Storage").json()
    assert {v["value"] for v in r["values"]} == {"Virtual Machines", "Storage", "SaaS", "Bandwidth"}
    s = get(client, "/api/dimensions/subscription", search="invented").json()
    assert [(v["label"], v["cost"]) for v in s["values"]] == [("invented-sub", "16.2500")]
    assert get(client, "/api/dimensions/subscription", search="zzz").json()["values"] == []


def test_reservations_and_meta(client):
    r = get(client, "/api/reservations").json()
    assert r["available"] and r["count"] == 2 and r["rows"][0]["commitment_id"] == "R1"
    assert r["rows"][0]["used_cost"] == "1.6000" and r["rows"][0]["utilization"] == pytest.approx(4 / 6, abs=1e-6)
    m = client.get("/api/meta").json()
    assert m["scopes"] == [{"cost_kind": "amortized", "currency": "GBP", "first_date": "2026-09-02",
                            "last_date": "2026-09-02", "rows": 9, "synthetic": False}]
    assert "service" in m["dimensions"]


def test_commitments_endpoint(client):
    r = get(client, "/api/commitments")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True and d["category_count"] == 1
    c = d["by_category"][0]
    assert c["category"] == "Virtual Machines" and c["gross"] == "2.0000" and c["net"] == "1.2000"
    assert c["coverage"] == pytest.approx(3.95 / 13.95, abs=1e-6)
    assert d["spend"]["used_cost"] == "1.6000" and d["by_month"][0]["month"] == "2026-09-01"
    assert client.get("/api/commitments", params={"cost_kind": "actual", "currency": "GBP"}).json()["available"] is False
    assert get(client, "/api/commitments", limit=0).status_code == 422


def test_movement_endpoint_validates_and_reports_missing_months(client):
    r = get(client, "/api/cost/movement", month_from="2026-08-01", month_to="2026-09-01")
    assert r.status_code == 200 and r.json()["available"] is False and "2026-08" in r.json()["reason"]
    for params in ({"month_from": "2026-08-15", "month_to": "2026-09-01"},
                   {"month_from": "2026-09-01", "month_to": "2026-09-01"},
                   {"month_from": "2026-08-01"}, {"month_from": "x", "month_to": "2026-09-01"}):
        assert get(client, "/api/cost/movement", **params).status_code == 422, params


def test_applications_endpoint(client):
    d = get(client, "/api/applications").json()
    assert d["total"] == "16.2500" and d["untagged"] == "13.7500" and d["attributed"] == "2.5000"
    assert d["attributed_share"] == pytest.approx(2.5 / 16.25, abs=1e-6)
    assert get(client, "/api/applications", n=0).status_code == 422


def test_pricing_and_workloads_endpoints(client):
    p = get(client, "/api/pricing").json()
    assert p["totals"]["cost"] == "15.4500" and p["reference"]["price_benchmarks"] == 0
    assert p["reference"]["subscriptions_named"] == 1
    vm = next(r for r in p["by_service"] if r["key"] == "Virtual Machines")
    assert vm["payg_equivalent"] == "13.9500" and vm["missing_rows"] == 0
    w = get(client, "/api/workloads").json()
    assert w["total"] == "16.2500" and w["outside_families"] == "16.2500" and w["months"] == ["2026-09-01"]
    assert [g["id"] for g in w["groups"]] == ["ai", "data", "database"]
    assert get(client, "/api/pricing", n=0).status_code == 422


def test_savings_headroom_endpoint_reports_a_missing_price_sheet(client):
    d = get(client, "/api/savings-headroom").json()          # the fixture database has no price sheet loaded
    assert d["available"] is False and "azfinops-pricesheet" in d["reason"] and d["totals"] is None
    assert d["by_service"] == [] and d["price_months"] == []
    assert get(client, "/api/savings-headroom", n=0).status_code == 422


def test_untrusted_host_header_is_rejected(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400


def test_database_connection_is_read_only(client):
    with client.app.state.db.cursor() as con:
        with pytest.raises(duckdb.Error):
            con.execute("CREATE TABLE should_fail (i INTEGER)")
        with pytest.raises(duckdb.Error):
            con.execute("DELETE FROM agg_daily")


def test_openapi_documents_the_contract(client):
    paths = client.get("/api/openapi.json").json()["paths"]
    for p in ("/api/overview", "/api/cost/explore", "/api/cost/series", "/api/dimensions/{dimension}",
              "/api/reservations", "/api/meta", "/api/health", "/api/data/imports", "/api/data/quality",
              "/api/sql/schema", "/api/cost/breakdown"):
        assert p in paths and set(paths[p]) == {"get"}       # read-only: GET only
    # the one exception: a single SELECT statement, guarded by a header and by sqlbench
    assert set(paths["/api/sql/run"]) == {"post"}
    assert sorted(p for p, ops in paths.items() if set(ops) - {"get"}) == [
        "/api/budgets", "/api/fx/query", "/api/fx/rates", "/api/import/start", "/api/sql/run", "/api/views"]


def test_built_frontend_is_served_with_csp_and_api_still_wins(db_path, tmp_path):
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    app = create_app(Settings(data_dir=db_path.parent), db_path, allowed_hosts=("testserver",), web_dir=web)
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200 and "default-src 'self'" in r.headers["content-security-policy"]
        assert c.get("/api/health").status_code == 200


@pytest.mark.parametrize("heavy", [False, True])
def test_query_deadline_interrupts_and_maps_to_504(db_path, tmp_path, heavy):
    app = create_app(Settings(data_dir=db_path.parent, query_timeout_s=1), db_path,
                     allowed_hosts=("testserver",), web_dir=tmp_path / "no-web")

    @app.get("/slow")
    def slow():
        with app.state.db.cursor(heavy=heavy) as con:
            con.execute("SELECT count(*) FROM range(100000000000) a, range(1000) b").fetchall()

    with TestClient(app) as c:
        r = c.get("/slow")
        assert r.status_code == 504 and "exceeded" in r.json()["error"]
        assert c.get("/api/health").status_code == 200            # server is still healthy afterwards
        with app.state.db.cursor(heavy=heavy) as con:
            assert con.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.parametrize("heavy", [False, True])
def test_concurrency_gate_rejects_instead_of_queueing_forever(db_path, monkeypatch, heavy):
    monkeypatch.setattr(dbmod, "GATE_WAIT_S", 0.2)
    d = Database(db_path, Settings(data_dir=db_path.parent, max_concurrent_queries=2 if heavy else 1))
    held, release = threading.Event(), threading.Event()

    def hold():
        with d.cursor(heavy=heavy):
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5)
        with pytest.raises(Overloaded), d.cursor(heavy=heavy):
            pass
    finally:
        release.set()
        t.join()
        d.close()


@pytest.mark.parametrize("path", ["/api/overview", "/api/overview/summary", "/api/fx/combined", "/api/data/quality"])
def test_portfolio_scans_wait_without_blocking_light_requests(client, monkeypatch, path):
    from concurrent.futures import ThreadPoolExecutor

    d = client.app.state.db
    waiting = threading.Event()
    acquire = d._heavy_gate.acquire

    def observe(*args, **kwargs):
        waiting.set()
        return acquire(*args, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with d.cursor(heavy=True):
            monkeypatch.setattr(d._heavy_gate, "acquire", observe)
            pending = pool.submit(get, client, path)
            assert waiting.wait(5)
            assert not pending.done()
            assert client.get("/api/health").status_code == 200
        response = pending.result(timeout=5)
    assert response.status_code == 200
    data = response.json()
    if path.startswith("/api/overview"):
        assert (data["totals"]["total"] if path == "/api/overview" else data["total"]) == "16.2500"


def test_queued_heavy_request_observes_import_pause_and_releases_its_gate(db_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    d = Database(db_path, Settings(data_dir=db_path.parent))
    waiting = threading.Event()
    acquire = d._heavy_gate.acquire

    def observe(*args, **kwargs):
        waiting.set()
        return acquire(*args, **kwargs)

    def query():
        with d.cursor(heavy=True) as con:
            return con.execute("SELECT 1").fetchone()[0]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            with d.cursor(heavy=True):
                monkeypatch.setattr(d._heavy_gate, "acquire", observe)
                pending = pool.submit(query)
                assert waiting.wait(5)
                pausing = pool.submit(d.pause)
                assert d._paused.wait(5)
            with pytest.raises(Paused):
                pending.result(timeout=5)
            pausing.result(timeout=5)
        d.resume()
        assert query() == 1
    finally:
        d.close()


def test_database_refuses_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        Database(tmp_path / "nope.duckdb", Settings(data_dir=tmp_path))


# ---- Data Management -------------------------------------------------------------------------------

SQL = {"X-AzFinOps": "1"}


def run_sql(client, sql, headers=SQL, **extra):
    return client.post("/api/sql/run", json={"sql": sql, **extra}, headers=headers)


def test_sql_requires_the_custom_header(client):
    assert run_sql(client, "SELECT 1", headers={}).status_code == 403
    assert run_sql(client, "SELECT 1", headers={"X-AzFinOps": "0"}).status_code == 403


def test_sql_select_runs_against_the_data(client):
    r = run_sql(client, "SELECT count(*) AS n, sum(cost) AS c FROM agg_daily")
    assert r.status_code == 200
    j = r.json()
    assert j["columns"] == ["n", "c"] and j["truncated"] is False and j["elapsed_ms"] >= 0
    assert j["rows"][0][0] >= 1 and j["rows"][0][1].startswith("16.25")


@pytest.mark.parametrize("sql", [
    "DROP TABLE agg_daily", "DELETE FROM agg_daily", "SELECT 1; DROP TABLE agg_daily", "ATTACH 'x.db'",
    "COPY agg_daily TO 'out.csv'", "PRAGMA database_list", "SET memory_limit='9GB'",
])
def test_sql_rejects_writes_and_multiple_statements(client, sql):
    r = run_sql(client, sql)
    assert r.status_code == 422 and r.json()["error"]
    assert run_sql(client, "SELECT count(*) FROM agg_daily").json()["rows"][0][0] >= 1


def test_sql_cannot_read_files_or_change_settings(client, db_path):
    r = run_sql(client, f"SELECT * FROM read_csv('{db_path.parent.as_posix()}/nope.csv')")
    assert r.status_code == 422
    r = run_sql(client, f"SELECT * FROM read_text('{(Path(__file__)).as_posix()}')")
    assert r.status_code == 422
    assert run_sql(client, "SELECT current_setting('enable_external_access')").json()["rows"][0][0] is False


def test_sql_row_cap_and_bad_sql_message(client):
    j = run_sql(client, "SELECT * FROM range(50)", limit=7).json()
    assert len(j["rows"]) == 7 and j["truncated"] is True
    r = run_sql(client, "SELECT * FROM does_not_exist")
    assert r.status_code == 422 and "does_not_exist" in r.json()["error"]


def test_sql_deadline_maps_to_504(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr("azfinops.api.app.SQL_TIMEOUT_S", 1)
    app = create_app(Settings(data_dir=db_path.parent), db_path, allowed_hosts=("testserver",),
                     web_dir=tmp_path / "no-web")
    with TestClient(app) as c:
        r = run_sql(c, "SELECT count(*) FROM range(100000000000) a, range(1000) b")
        assert r.status_code == 504


def test_sql_schema_lists_tables_and_views(client):
    t = {x["name"]: x for x in client.get("/api/sql/schema").json()["tables"]}
    assert {"agg_daily", "cost_facts", "import_batches", "v_cost_facts"} <= set(t)
    assert t["v_cost_facts"]["kind"] == "view" and t["agg_daily"]["kind"] == "table"
    assert "cost" in [c["name"] for c in t["agg_daily"]["columns"]]


def test_import_history_has_batch_files_and_counts(client):
    j = client.get("/api/data/imports").json()
    assert j["total_batches"] == 1
    b = j["batches"][0]
    assert b["status"] == "committed" and b["rows"] == 9 and b["synthetic"] is False
    assert b["rows"] + (b["rejected"] or 0) == 11
    assert [f["file_name"] for f in b["files"]] == ["ea_amortized_sep.csv"] and len(b["files"][0]["sha256"]) == 12
    assert j["price_sheet_files"] == []


def test_quality_checks_on_the_fixture(client):
    j = client.get("/api/data/quality").json()
    by = {c["id"]: c for c in j["checks"]}
    assert by["aggregates_match"]["status"] == "info" and j["full_reconciliation"] is False
    assert by["batches"]["status"] == "pass" and by["synthetic"]["status"] == "pass"
    assert by["rejected"]["status"] == "warn" and by["rejected"]["numbers"]["rejected"] == "2"
    assert by["price_sheet"]["status"] == "info"
    assert sum(j["summary"].values()) == len(j["checks"])


def test_full_quality_reconciliation_is_explicit(client):
    response = client.get("/api/data/quality", params={"full": "true"})
    assert response.status_code == 200 and response.json()["full_reconciliation"] is True
    by = {c["id"]: c for c in response.json()["checks"]}
    assert by["aggregates_match"]["status"] == "pass"
    assert by["resource_aggregate"]["status"] == "pass"


def test_fast_quality_checks_never_query_raw_facts(client, monkeypatch):
    from azfinops import datamgmt

    def forbidden(_con):
        pytest.fail("raw reconciliation must not run automatically")

    monkeypatch.setattr(datamgmt, "_reconciliation", forbidden)
    assert client.get("/api/data/quality").status_code == 200


def test_full_reconciliation_detects_a_mismatch_not_certified_by_fast_checks(tmp_path):
    from azfinops import datamgmt

    p = tmp_path / "mismatch.duckdb"
    migrate(p)
    import_files(p, [MAIN], "amortized", max_reject_ratio=1.0)
    rebuild(p, Settings(data_dir=tmp_path))
    with duckdb.connect(str(p)) as con:
        con.execute("UPDATE agg_daily SET cost = cost + 1 WHERE meter_category = 'Storage'")
        fast = {c["id"]: c for c in datamgmt.quality_checks(con)["checks"]}
        full = {c["id"]: c for c in datamgmt.quality_checks(con, full=True)["checks"]}
    assert fast["aggregates_match"]["status"] == "info"
    assert full["aggregates_match"]["status"] == "fail"
    assert "cost difference -1" in full["aggregates_match"]["detail"]


def test_service_breakdown_adds_up_and_matches_the_explorer(client):
    ex = get(client, "/api/cost/explore", group_by="service").json()["rows"]
    assert ex
    for row in ex:
        service = row["keys"][0]
        b = get(client, "/api/cost/breakdown", service=service).json()
        assert b["available"] is True and b["service"] == service
        assert b["total"] == row["cost"]                                   # same figure as the Explorer row
        shown = sum(float(r["cost"]) for r in b["rows"])
        assert abs(shown + float(b["other"]) - float(b["total"])) < 1e-3   # meters plus the rest equal the total
        assert b["groups"] >= len(b["rows"]) >= 1
        assert all(r["share"] is None or 0 <= abs(r["share"]) <= 1.0001 for r in b["rows"])
        names = [(r["name"], r["unit"]) for r in b["rows"]]
        assert len(names) == len(set(names))                               # one row per meter name and unit
        for r in b["rows"]:
            assert r["variants"] >= len(r["parts"]) >= 1
            if r["variants"] == len(r["parts"]):
                assert abs(sum(float(x["cost"]) for x in r["parts"]) - float(r["cost"])) < 1e-3


def test_service_breakdown_unknown_service_and_row_cap(client):
    b = get(client, "/api/cost/breakdown", service="No Such Service").json()
    assert b["available"] is False and b["rows"] == []
    ex = get(client, "/api/cost/explore", group_by="service").json()["rows"]
    top = get(client, "/api/cost/breakdown", service=ex[0]["keys"][0], n=1).json()
    assert len(top["rows"]) == 1
    assert get(client, "/api/cost/breakdown").status_code == 422           # service is required
    assert get(client, "/api/cost/breakdown", service="x" * 300).status_code == 422


# ---- Exchange rates --------------------------------------------------------------------------------

def _fake_fetch(url):
    gbp = "GBP" in url
    items = [{"meterId": f"m{i}", "skuId": "s", "unitOfMeasure": "1 Hour", "type": "Consumption",
              "retailPrice": (0.8 if gbp else 1.0) * (i + 1)} for i in range(30)]
    items.append({"meterId": "zero", "skuId": "s", "unitOfMeasure": "1 Hour", "type": "Consumption", "retailPrice": 0})
    return {"Items": items}


@pytest.fixture
def fx_client(db_path, tmp_path, monkeypatch):
    from azfinops import fx
    monkeypatch.setattr(fx, "http_get", _fake_fetch)
    app = create_app(Settings(data_dir=tmp_path), db_path, allowed_hosts=("testserver",), web_dir=tmp_path / "no-web")
    with TestClient(app) as c:
        yield c


def test_fx_query_needs_header_and_returns_the_median_rate(fx_client):
    body = {"from_currency": "USD", "to_currency": "GBP"}
    assert fx_client.post("/api/fx/query", json=body).status_code == 403
    r = fx_client.post("/api/fx/query", json=body, headers=SQL)
    assert r.status_code == 200
    j = r.json()
    assert j["rate"] == "0.800000" and j["matched"] == 30 and j["from"] == "USD" and j["to"] == "GBP"
    assert fx_client.get("/api/fx/rates").json() == {"rates": []}       # a query stores nothing


def test_fx_query_rejects_bad_currencies(fx_client):
    for body in ({"from_currency": "usd", "to_currency": "GBP"}, {"from_currency": "USD", "to_currency": "USD"}):
        assert fx_client.post("/api/fx/query", json=body, headers=SQL).status_code == 422


def test_fx_save_replaces_the_same_month_and_validates(fx_client):
    def save(**kw):
        b = {"month": "2026-09", "from_currency": "USD", "to_currency": "GBP", "rate": "0.8", "method": "manual", **kw}
        return fx_client.post("/api/fx/rates", json=b, headers=SQL)
    assert fx_client.post("/api/fx/rates", json={}).status_code in (403, 422)
    assert save().status_code == 200
    assert save(rate="0.79").status_code == 200
    rates = fx_client.get("/api/fx/rates").json()["rates"]
    assert [(r["month"], r["rate"]) for r in rates] == [("2026-09", "0.79")]
    for bad in ({"rate": "0"}, {"rate": "-1"}, {"rate": "abc"}, {"rate": "NaN"}, {"month": "2026-13"}, {"method": "x"}):
        assert save(**bad).status_code == 422


def test_fx_too_few_matches_is_an_error(fx_client, monkeypatch):
    from azfinops import fx
    monkeypatch.setattr(fx, "http_get", lambda url: {"Items": []})
    r = fx_client.post("/api/fx/query", json={"from_currency": "USD", "to_currency": "GBP"}, headers=SQL)
    assert r.status_code == 422


def test_fx_unreachable_is_502(fx_client, monkeypatch):
    from azfinops import fx

    def boom(url):
        raise fx.FxUnavailable("offline")
    monkeypatch.setattr(fx, "http_get", boom)
    r = fx_client.post("/api/fx/query", json={"from_currency": "USD", "to_currency": "GBP"}, headers=SQL)
    assert r.status_code == 502


def test_overview_breaks_the_total_down_by_enrollment(client):
    o = get(client, "/api/overview", **BASE).json()
    rows = o["by_account"]
    assert rows and abs(sum(float(r["cost"]) for r in rows) - float(o["totals"]["total"])) < 0.01
    assert abs(sum(r["share"] for r in rows) - 1.0) < 1e-9


def test_overview_by_account_lists_every_currency_in_a_single_currency_view(client):
    rows = get(client, "/api/overview", **BASE).json()["by_account"]
    assert {r["currency"] for r in rows} == {"GBP"}


def test_overview_by_enrollment_month_and_enrollment_dimension(client):
    o = get(client, "/api/overview", **BASE).json()
    assert o["by_account_month"] and all(r["account"] and r["month"] for r in o["by_account_month"])
    assert abs(sum(float(r["cost"]) for r in o["by_account_month"]) - float(o["totals"]["total"])) < 0.01
    r = get(client, "/api/cost/explore", group_by="billing_account", **BASE).json()
    assert r["rows"] and abs(sum(float(x["cost"]) for x in r["rows"]) - float(o["totals"]["total"])) < 0.01


def test_movers_and_monthly_by_enrollment_endpoints(client):
    r = get(client, "/api/cost/movers", dimension="subscription")
    assert r.status_code == 200 and r.json()["available"] is False
    assert "complete months" in r.json()["reason"] and r.json()["increases"] == []
    assert get(client, "/api/cost/movers", dimension="nope").status_code == 422
    m = get(client, "/api/accounts/monthly").json()
    o = get(client, "/api/overview").json()
    assert m == o["by_account_month"]


def test_overview_can_leave_out_lists_and_each_list_loads_on_its_own(client):
    o = get(client, "/api/overview", tops="service").json()
    assert set(o["top"]) == {"service"} and o["pay_models"]["overall"]
    assert get(client, "/api/overview", tops="nope").status_code == 422
    full = get(client, "/api/overview").json()["top"]["subscription"]
    one = get(client, "/api/cost/top", dimension="subscription").json()
    assert one == full and one["rows"][0]["label"] == "invented-sub"
    assert get(client, "/api/cost/top", dimension="nope").status_code == 422


def test_resource_changes_endpoint(client):
    r = get(client, "/api/optimization/resource-changes")
    assert r.status_code == 200
    assert set(r.json()["totals"]) == {"decommissioned", "downsized", "upsized"}
    assert "run-rate" in r.json()["definition"]


def test_savings_plan_hourly_endpoint(client):
    r = get(client, "/api/commitments/savings-plan-hourly")
    assert r.status_code == 200 and "per day / 24" in r.json()["definition"]


def test_savings_headroom_in_the_combined_view_says_unavailable_instead_of_failing(client):
    r = get(client, "/api/savings-headroom", combined="1")
    assert r.status_code == 200 and r.json()["available"] is False and "combined" in r.json()["reason"]


def test_meta_lists_each_enrollments_currency(client):
    m = client.get("/api/meta").json()
    assert m["billing_accounts"] and all(m["billing_account_currencies"][a] for a in m["billing_accounts"])


def test_cost_flow_endpoint(client):
    r = get(client, "/api/cost/flow", levels="billing_account,service,subscription")
    assert r.status_code == 200 and r.json()["paths"]
    assert "invented-sub" in r.json()["labels"]["subscription"].values()
    assert get(client, "/api/cost/flow", levels="service").status_code == 422


def test_cost_analysis_endpoint_with_previous_period(client):
    r = get(client, "/api/cost/analysis", dimension="subscription", grain="day", compare="true")
    assert r.status_code == 200 and r.json()["available"]
    j = r.json()
    assert j["series"][0]["label"] == "invented-sub" and len(j["series"][0]["values"]) == len(j["periods"])
    assert j["previous"] is None and "complete imported months" in j["comparison_reason"]
    assert get(client, "/api/cost/analysis", dimension="nope").status_code == 422


def test_budgets_and_views_need_the_header(client):
    assert client.post("/api/budgets", json={"scope": "all", "currency": "GBP", "amount": "100"}).status_code == 403
    r = client.post("/api/budgets", json={"scope": "all", "currency": "GBP", "amount": "100"}, headers={"X-AzFinOps": "1"})
    assert r.status_code == 200 and r.json()["budgets"][0]["amount"] == "100"
    assert client.post("/api/views", json={"name": "x", "query": "a<b"}, headers={"X-AzFinOps": "1"}).status_code == 422


def test_combined_endpoint_preserves_exact_rounding_at_a_four_decimal_tie(tmp_path):
    from datetime import date

    from azfinops import fx
    from azfinops.aggregates import _build_scope_freshness

    p = tmp_path / "precise.duckdb"
    migrate(p)
    with duckdb.connect(str(p)) as con:
        con.execute("INSERT INTO agg_daily (usage_date, usage_month, cost_kind, billing_account_id, billing_currency, "
                    "charge_type, application, environment, cost, payg_equivalent, cost_with_payg, payg_missing_rows, "
                    "contract_basis_rows, row_count, is_synthetic) VALUES "
                    "(DATE '2026-09-01', DATE '2026-09-01', 'amortized', 'a', 'GBP', 'Usage', 'x', 'e', "
                    "1.0000625, 1, 1, 0, 0, 1, false)")
        _build_scope_freshness(con, date(2026, 9, 1))
    fx.save(tmp_path, "2026-09", "GBP", "USD", "0.8", "manual", "synthetic regression")
    app = create_app(Settings(data_dir=tmp_path), p, allowed_hosts=("testserver",), web_dir=tmp_path / "no-web")
    with TestClient(app) as c:
        response = c.get("/api/overview", params={"cost_kind": "amortized", "currency": "USD", "combined": "true"})
        assert response.status_code == 200 and response.json()["totals"]["total"] == "0.8000"


def test_concurrent_combined_requests_do_not_share_currency_labels_or_money_caches(db_path, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from azfinops import fx

    fx.save(tmp_path, "2026-09", "GBP", "USD", "0.8", "manual", "concurrent synthetic regression")
    app = create_app(Settings(data_dir=tmp_path), db_path, allowed_hosts=("testserver",), web_dir=tmp_path / "no-web")
    with TestClient(app) as c:
        def total(currency):
            response = c.get("/api/overview", params={"cost_kind": "amortized", "currency": currency,
                             "combined": "true", "start": "2026-09-01", "end": "2026-09-30", "tops": ""})
            assert response.status_code == 200, response.text
            return response.json()["totals"]["total"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(total, ["GBP", "USD"] * 4))
        assert results == ["16.2500", "13.0000"] * 4
