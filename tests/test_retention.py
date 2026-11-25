"""The newest imported cost month anchors one 12-calendar-month window for the whole portfolio."""
import csv
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from azfinops import retention
from azfinops.aggregates import affected_months, rebuild
from azfinops.api.app import create_app
from azfinops.config import Settings
from azfinops.ingestion.importer import ImportRejected, import_files
from azfinops.migrations.runner import migrate
from azfinops.pricesheet import import_price_sheets

Row = tuple[str, str, str, str, str]
HEADER = ["Date", "BillingAccountId", "BillingCurrency", "Cost", "ChargeType"]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "azfinops.duckdb"
    migrate(path)
    return path


def seed(con, batch, rows, kind="amortized", status="committed"):
    days = [date.fromisoformat(r[0]) for r in rows]
    con.execute(
        "INSERT INTO import_batches (batch_id, source_kind, cost_kind, is_synthetic, status, "
        "period_start, period_end, row_count, rejected_row_count, created_at, finished_at) "
        "VALUES (?, 'csv', ?, false, ?, ?, ?, ?, 0, now(), now())",
        [batch, kind, status, min(days), max(days), len(rows)])
    con.execute("INSERT INTO import_files (file_id, batch_id, file_name, sha256) VALUES (?, ?, ?, ?)",
                [batch, batch, f"source-{batch}.csv", f"fixture-{batch}"])
    for number, (day, account, currency, cost, charge) in enumerate(rows, 1):
        used = date.fromisoformat(day)
        con.execute(
            "INSERT INTO cost_facts (batch_id, file_id, row_number, cost_kind, usage_date, usage_month, "
            "billing_account_id, billing_currency, cost, charge_type, is_synthetic) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, false)",
            [batch, batch, number, kind, used, used.replace(day=1), account, currency, Decimal(cost), charge])


def export(path: Path, rows: list[Row]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def twelve_months(con):
    seed(con, 1, [(f"2025-{m:02}-01", "a", "GBP", str(m), "Usage") for m in range(1, 13)])


def apply_import(db, path, kind="amortized", **kwargs):
    result = import_files(db, [path], kind, **kwargs)
    with duckdb.connect(str(db), read_only=True) as con:
        months = affected_months(con, [result.batch_id, *result.superseded_batches])
    rebuild(db, Settings(data_dir=db.parent), months)
    return result


def test_thirteenth_month_expires_the_oldest_month_and_all_derived_rows(db, tmp_path):
    with duckdb.connect(str(db)) as con:
        twelve_months(con)
    rebuild(db)
    incoming = export(tmp_path / "usage-new.csv", [("2026-01-01", "a", "GBP", "13", "Usage")])
    before_bytes = incoming.read_bytes()
    result = apply_import(db, incoming)
    assert result.retention.cutoff == date(2025, 2, 1)
    assert result.retention.newest_month == date(2026, 1, 1)
    assert result.retention.fact_rows == 1 and result.rows_retained == 1
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT min(usage_month), max(usage_month), count(*), sum(cost) FROM v_cost_facts").fetchone() == (
            date(2025, 2, 1), date(2026, 1, 1), 12, Decimal(90))  # 2 + ... + 13
        for table in retention.DERIVED_TABLES:
            assert con.execute(f"SELECT count(*) FROM {table} WHERE usage_month < DATE '2025-02-01'").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM aggregate_invalidations").fetchone()[0] == 0
        assert con.execute("SELECT row_count FROM import_batches WHERE batch_id = 1").fetchone()[0] == 12
        assert con.execute("SELECT sample_value FROM validation_results WHERE code = ?", [retention.AUDIT_CODE]).fetchone()[0] == "1"
    assert incoming.read_bytes() == before_bytes


def test_cutoff_is_shared_across_enrollments_currencies_bases_and_superseded_facts(db, tmp_path):
    with duckdb.connect(str(db)) as con:
        seed(con, 1, [
            ("2025-01-01", "a", "GBP", "5", "Usage"),
            ("2025-01-02", "a", "USD", "-2", "Credit"),
            ("2025-02-01", "a", "GBP", "3", "Usage"),
            ("2025-12-01", "a", "GBP", "-4", "Refund"),
        ])
        seed(con, 2, [("2025-01-01", "other", "EUR", "8", "Usage")], kind="actual")
        seed(con, 3, [("2025-01-01", "old", "USD", "6", "Usage")], status="superseded")
    rebuild(db)
    result = apply_import(db, export(tmp_path / "actual.csv", [("2026-01-01", "new", "USD", "7", "Usage")]), "actual")
    assert result.retention.fact_rows == 4
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM cost_facts WHERE usage_month < DATE '2025-02-01'").fetchone()[0] == 0
        assert con.execute("SELECT cost_kind, billing_currency, sum(cost) FROM v_cost_facts GROUP BY 1, 2 ORDER BY 1").fetchall() == [
            ("actual", "USD", Decimal(7)), ("amortized", "GBP", Decimal(-1))]


def test_partially_old_multi_month_export_reports_accepted_and_retained_rows(db, tmp_path):
    with duckdb.connect(str(db)) as con:
        seed(con, 1, [("2026-01-01", "existing", "GBP", "10", "Usage")])
    path = export(tmp_path / "mixed.csv", [
        ("2025-01-31", "a", "GBP", "99", "Usage"),
        ("2025-02-01", "a", "GBP", "-2", "Credit"),
        ("2025-12-01", "a", "USD", "7", "Usage"),
    ])
    result = apply_import(db, path)
    assert result.rows_loaded == 3 and result.rows_retained == 2
    assert result.retention.rows_by_batch[result.batch_id] == 1
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM v_cost_facts").fetchone()[0] == 3
        assert con.execute("SELECT row_count FROM import_batches WHERE batch_id = ?", [result.batch_id]).fetchone()[0] == 3


def test_old_only_export_is_refused_without_deleting_existing_data(db, tmp_path):
    with duckdb.connect(str(db)) as con:
        seed(con, 1, [("2026-01-01", "a", "GBP", "10", "Usage")])
    path = export(tmp_path / "old.csv", [("2025-01-31", "a", "GBP", "99", "Usage")])
    with pytest.raises(ImportRejected, match="before the retained window"):
        import_files(db, [path], "amortized")
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT cost FROM v_cost_facts").fetchall() == [(Decimal(10),)]


def test_rejected_new_month_does_not_expire_old_data(db, tmp_path):
    with duckdb.connect(str(db)) as con:
        twelve_months(con)
    path = export(tmp_path / "bad.csv", [("2026-01-01", "a", "GBP", "not-a-number", "Usage")])
    with pytest.raises(ImportRejected, match="valid data rows"):
        import_files(db, [path], "amortized")
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT min(usage_month), count(*) FROM v_cost_facts").fetchone() == (date(2025, 1, 1), 12)


def test_purge_failure_rolls_back_old_data_new_commit_and_audit(db, tmp_path, monkeypatch):
    with duckdb.connect(str(db)) as con:
        twelve_months(con)
    rebuild(db)
    original = retention.purge

    def fail_after_deletion(con, candidate):
        original(con, candidate)
        raise RuntimeError("injected retention failure")

    monkeypatch.setattr(retention, "purge", fail_after_deletion)
    path = export(tmp_path / "new.csv", [("2026-01-01", "a", "GBP", "13", "Usage")])
    with pytest.raises(RuntimeError, match="injected"):
        import_files(db, [path], "amortized")
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT min(usage_month), count(*), sum(cost) FROM v_cost_facts").fetchone() == (
            date(2025, 1, 1), 12, Decimal(78))
        assert con.execute("SELECT sum(cost) FROM agg_daily").fetchone()[0] == Decimal(78)
        assert con.execute("SELECT count(*) FROM validation_results WHERE code = ?", [retention.AUDIT_CODE]).fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM aggregate_invalidations").fetchone()[0] == 0


def test_calendar_window_does_not_keep_twelve_nonconsecutive_observed_months(db):
    with duckdb.connect(str(db)) as con:
        seed(con, 1, [("2023-01-01", "a", "GBP", "1", "Usage"), ("2026-01-01", "a", "GBP", "2", "Usage")])
        candidate = retention.plan(con)
    assert candidate.cutoff == date(2025, 2, 1) and candidate.fact_rows == 1


@pytest.mark.parametrize("latest,expected", [
    (date(2026, 1, 31), date(2025, 2, 1)),
    (date(2026, 10, 15), date(2025, 11, 1)),
    (date(2024, 2, 29), date(2023, 3, 1)),
    (date(1, 1, 1), date(1, 1, 1)),
])
def test_window_counts_calendar_months_across_year_and_leap_boundaries(db, latest, expected):
    with duckdb.connect(str(db)) as con:
        assert retention.window(con, latest) == (latest.replace(day=1), expected)


def test_future_month_cannot_trigger_a_destructive_retention_jump(db, tmp_path):
    with duckdb.connect(str(db)) as con:
        twelve_months(con)
    path = export(tmp_path / "future.csv", [("9999-12-01", "a", "GBP", "13", "Usage")])
    with pytest.raises(ImportRejected, match="future"):
        import_files(db, [path], "amortized")
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM v_cost_facts").fetchone()[0] == 12


def test_existing_purge_backs_up_preserves_reference_data_and_is_idempotent(db, tmp_path):
    with duckdb.connect(str(db)) as con:
        seed(con, 1, [("2025-01-01", "a", "GBP", "1", "Usage"), ("2026-01-01", "a", "GBP", "2", "Usage")])
    rebuild(db)
    fixture = Path(__file__).parent / "fixtures" / "price_sheet" / "invented_price_sheet_sep.csv"
    import_price_sheets(db, [fixture])
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE price_sheet SET effective_start = DATE '2020-01-01'")
        price_rows = con.execute("SELECT count(*) FROM price_sheet").fetchone()[0]
    settings = tmp_path / "fx_rates.json"
    settings.write_text("[]")
    backups = [tmp_path / f"azfinops.duckdb.bak-2025010{d}T000000Z" for d in range(1, 4)]
    for path in backups:
        path.write_bytes(b"existing backup")
    result = retention.apply_existing(db)
    assert result.fact_rows == 1
    new_backup = next(p for p in tmp_path.glob("azfinops.duckdb.bak-*") if p not in backups)
    with duckdb.connect(str(new_backup), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM v_cost_facts").fetchone()[0] == 2
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT count(*), sum(cost) FROM v_cost_facts").fetchone() == (1, Decimal(2))
        assert con.execute("SELECT count(*) FROM price_sheet").fetchone()[0] == price_rows
    assert all(p.read_bytes() == b"existing backup" for p in backups)
    assert settings.read_text() == "[]"
    assert retention.apply_existing(db).has_changes is False
    assert len(list(tmp_path.glob("azfinops.duckdb.bak-*"))) == 4


def test_backup_failure_prevents_existing_data_purge(db, monkeypatch):
    with duckdb.connect(str(db)) as con:
        seed(con, 1, [("2025-01-01", "a", "GBP", "1", "Usage"), ("2026-01-01", "a", "GBP", "2", "Usage")])

    def fail_backup(*args, **kwargs):
        raise OSError("backup failed")

    monkeypatch.setattr(retention, "backup_database", fail_backup)
    with pytest.raises(OSError, match="backup failed"):
        retention.apply_existing(db)
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM v_cost_facts").fetchone()[0] == 2


def test_audit_counts_accumulate_when_one_original_batch_expires_in_stages(db, tmp_path):
    with duckdb.connect(str(db)) as con:
        twelve_months(con)
    for month in (1, 2):
        apply_import(db, export(tmp_path / f"new-{month}.csv", [(f"2026-{month:02}-01", "a", "GBP", "13", "Usage")]))
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT sample_value FROM validation_results WHERE batch_id = 1 AND code = ?",
                           [retention.AUDIT_CODE]).fetchall() == [("2",)]


def test_dashboard_import_applies_the_same_retention_and_reports_it(db, tmp_path):
    with duckdb.connect(str(db)) as con:
        twelve_months(con)
    rebuild(db)
    export(tmp_path / "usage-new.csv", [("2026-01-01", "a", "GBP", "13", "Usage")])
    settings = Settings(data_dir=tmp_path, import_dirs=(tmp_path,))
    app = create_app(settings, db, allowed_hosts=("testserver",), web_dir=tmp_path / "no-web")
    with TestClient(app) as client:
        response = client.post("/api/import/start", headers={"X-AzFinOps": "1"},
                               json={"kind": "usage", "cost_kind": "amortized", "files": ["0/usage-new.csv"]})
        assert response.status_code == 200
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = client.get("/api/import/status").json()
            if state["state"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert state["state"] == "done", state
        assert "12-month window 2025-02..2026-01" in state["results"][0]["summary"]
        meta = client.get("/api/meta").json()
        assert meta["scopes"][0]["first_date"] == "2025-02-01" and not meta["recovery_required"]


def test_command_line_import_applies_retention_and_reports_the_window(db, tmp_path, monkeypatch, capsys):
    from azfinops.ingestion.__main__ import main

    with duckdb.connect(str(db)) as con:
        twelve_months(con)
    path = export(tmp_path / "usage-new.csv", [("2026-01-01", "a", "GBP", "13", "Usage")])
    monkeypatch.setenv("AZFINOPS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["azfinops-import", "--db", str(db), "--kind", "amortized", str(path)])
    main()
    assert "12-month window 2025-02..2026-01" in capsys.readouterr().out
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT min(usage_month), count(*) FROM v_cost_facts").fetchone() == (date(2025, 2, 1), 12)


def test_post_commit_rebuild_failure_keeps_expired_data_purged_and_blocks_stale_reads(db, tmp_path, monkeypatch):
    with duckdb.connect(str(db)) as con:
        twelve_months(con)
    rebuild(db)
    export(tmp_path / "usage-new.csv", [("2026-01-01", "a", "GBP", "13", "Usage")])
    settings = Settings(data_dir=tmp_path, import_dirs=(tmp_path,))
    app = create_app(settings, db, allowed_hosts=("testserver",), web_dir=tmp_path / "no-web")

    def fail_rebuild(*args, **kwargs):
        raise RuntimeError("injected post-commit failure")

    with TestClient(app) as client:
        with monkeypatch.context() as patch:
            patch.setattr("azfinops.aggregates.rebuild", fail_rebuild)
            assert client.post("/api/import/start", headers={"X-AzFinOps": "1"},
                               json={"kind": "usage", "files": ["0/usage-new.csv"]}).status_code == 200
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = client.get("/api/import/status").json()
                if state["state"] in ("done", "failed"):
                    break
                time.sleep(0.1)
        assert state["state"] == "failed" and state["data_committed"] and state["recovery_required"]
        response = client.get("/api/overview", params={"cost_kind": "amortized", "currency": "GBP"})
        assert response.status_code == 503
        with app.state.db.cursor(require_current=False) as con:
            assert con.execute("SELECT min(usage_month), count(*) FROM v_cost_facts").fetchone() == (date(2025, 2, 1), 12)
            assert con.execute("SELECT count(*) FROM agg_daily WHERE usage_month = DATE '2025-01-01'").fetchone()[0] == 0


def test_empty_database_needs_no_retention_backup(db, tmp_path):
    assert retention.apply_existing(db).has_changes is False
    assert list(tmp_path.glob("azfinops.duckdb.bak-*")) == []
