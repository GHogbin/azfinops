"""Imports started from the dashboard: file listing and resolution, the job runner, and the paused connection."""
import shutil
import time
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from azfinops import jobs
from azfinops.aggregates import rebuild
from azfinops.api.app import create_app
from azfinops.api.db import Database, StaleAggregates
from azfinops.config import Settings
from azfinops.ingestion.importer import import_files
from azfinops.migrations.runner import migrate

FIX = Path(__file__).parent / "fixtures"
MAIN = FIX / "ea_amortized" / "ea_amortized_sep.csv"
SHEET = FIX / "price_sheet" / "invented_price_sheet_sep.csv"
RESUB = FIX / "ea_amortized" / "overlap_resubmission.csv"
H = {"X-AzFinOps": "1"}


def test_partitions_of_one_export_form_one_batch():
    p = [Path("x/Detail_0.csv"), Path("x/Detail_1.csv"), Path("x/Other.csv"), Path("x/Sep_2026.csv")]
    assert jobs.group_parts(p) == [[Path("x/Detail_0.csv"), Path("x/Detail_1.csv")], [Path("x/Other.csv")],
                                   [Path("x/Sep_2026.csv")]]


def test_kind_is_guessed_from_the_name():
    assert jobs.guess_kind("ArmPriceSheet_Enrollment_100001_202608_0.csv") == "pricesheet"
    assert jobs.guess_kind("Detail_Enrollment_200001_202609_en.csv") == "usage"
    assert jobs.guess_kind("notes.csv") is None


def test_only_files_inside_the_allowed_folders_resolve(tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / "a.csv").write_text("x")
    (tmp_path / "outside.csv").write_text("x")
    s = Settings(data_dir=tmp_path, import_dirs=(drop,))
    assert jobs.resolve(s, "0/a.csv") == (drop / "a.csv").resolve()
    for bad in ("0/../outside.csv", "1/a.csv", "0/", "x/a.csv", "0/missing.csv", "0/..\\outside.csv"):
        with pytest.raises(ValueError):
            jobs.resolve(s, bad)


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "t.duckdb"
    drop = tmp_path / "drop"
    drop.mkdir()
    shutil.copy(MAIN, drop / MAIN.name)
    shutil.copy(SHEET, drop / SHEET.name)
    shutil.copy(RESUB, drop / RESUB.name)
    s = Settings(data_dir=tmp_path, import_dirs=(drop,), app_tag_keys=("app",))
    migrate(db)
    import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    rebuild(db, s)
    app = create_app(s, db, allowed_hosts=("testserver",), web_dir=tmp_path / "no-web")
    with TestClient(app) as c:
        yield c


def wait(c, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = c.get("/api/import/status").json()
        if st["state"] in ("done", "failed"):
            return st
        time.sleep(0.2)
    raise AssertionError("import did not finish")


def ids(c):
    return {f["name"]: f for f in c.get("/api/import/files").json()["files"]}


def test_listing_marks_files_already_imported(setup):
    f = ids(setup)
    assert f[MAIN.name]["imported"] is True
    assert f[SHEET.name]["imported"] is False and f[SHEET.name]["kind"] == "pricesheet"


def test_a_price_sheet_imports_and_the_dashboard_comes_back(setup):
    assert setup.get("/api/import/status").json()["state"] == "idle"
    body = {"kind": "pricesheet", "files": [ids(setup)[SHEET.name]["id"]]}
    assert setup.post("/api/import/start", json=body).status_code == 403          # needs the header
    r = setup.post("/api/import/start", json=body, headers=H)
    assert r.status_code == 200 and r.json()["state"] == "running"
    st = wait(setup)
    assert st["state"] == "done" and st["results"][0]["rows_loaded"] > 0, st
    assert setup.get("/api/meta").status_code == 200                              # read-only connection reopened
    assert ids(setup)[SHEET.name]["imported"] is True


def test_a_refused_usage_import_is_reported_and_the_dashboard_comes_back(setup):
    body = {"kind": "usage", "files": [ids(setup)[MAIN.name]["id"]], "supersede": True}
    assert setup.post("/api/import/start", json=body, headers=H).status_code == 200
    st = wait(setup)
    assert st["state"] == "failed" and "identical file" in st["error"]
    assert setup.get("/api/overview", params={"cost_kind": "amortized", "currency": "GBP"}).status_code == 200


def test_unknown_files_are_refused(setup):
    r = setup.post("/api/import/start", json={"kind": "usage", "files": ["0/../t.duckdb"]}, headers=H)
    assert r.status_code == 422


def test_failed_rebuild_blocks_stale_costs_persists_after_reopen_and_can_recover(setup, monkeypatch):
    from decimal import Decimal

    def fail_rebuild(*args, **kwargs):
        raise RuntimeError("injected aggregate failure")

    body = {"kind": "usage", "files": [ids(setup)[RESUB.name]["id"]], "supersede": True}
    with monkeypatch.context() as patch:
        patch.setattr("azfinops.aggregates.rebuild", fail_rebuild)
        assert setup.post("/api/import/start", json=body, headers=H).status_code == 200
        state = wait(setup)
    assert state["state"] == "failed" and state["data_committed"] and state["recovery_required"]
    response = setup.get("/api/overview", params={"cost_kind": "amortized", "currency": "GBP"})
    assert response.status_code == 503 and response.json()["recovery_required"]
    assert setup.get("/api/meta").json()["recovery_required"]
    assert setup.get("/api/data/imports").status_code == 200
    checks = setup.get("/api/data/quality").json()["checks"]
    assert next(c for c in checks if c["id"] == "aggregates_current")["status"] == "fail"
    database = setup.app.state.db
    with database.cursor(require_current=False) as con:
        assert con.execute("SELECT sum(cost) FROM v_cost_facts").fetchone()[0] == Decimal(9)
        assert con.execute("SELECT sum(cost) FROM agg_daily").fetchone()[0] == Decimal("16.25")
    database.pause()
    try:
        reopened = Database(database.path, database.settings)
        try:
            with pytest.raises(StaleAggregates), reopened.cursor():
                pytest.fail("stale costs must not be readable")
        finally:
            reopened.close()
        rebuild(database.path, database.settings)
    finally:
        database.resume()
    assert not setup.get("/api/meta").json()["recovery_required"]
    assert setup.get("/api/overview", params={"cost_kind": "amortized", "currency": "GBP"}).json()["totals"]["total"] == "9.0000"
