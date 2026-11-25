"""Needs duckdb. Skipped automatically where duckdb is not installed."""
import pytest

duckdb = pytest.importorskip("duckdb")

from azfinops.migrations.runner import discover, migrate


def test_migrate_creates_schema_and_is_idempotent(tmp_path):
    db = tmp_path / "t.duckdb"
    assert migrate(db) == [v for v, _, _ in discover()]
    assert migrate(db) == []
    con = duckdb.connect(str(db))
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert {"cost_facts", "import_batches", "agg_daily", "aggregate_freshness"} <= tables


def test_checksum_drift_is_rejected(tmp_path, monkeypatch):
    db = tmp_path / "t.duckdb"
    migrate(db)
    con = duckdb.connect(str(db))
    con.execute("UPDATE schema_migrations SET checksum = 'x'")
    con.close()
    with pytest.raises(RuntimeError):
        migrate(db)


def test_migration_string_literals_are_safe_for_the_statement_splitter():
    """The runner splits on ';' and strips '--' comments, so literals must not contain either."""
    import re

    from azfinops.migrations.runner import MIGRATIONS_DIR
    for p in MIGRATIONS_DIR.glob("0*.sql"):
        for ln in p.read_text().splitlines():
            code = ln.split("--", 1)[0]
            assert code.count("'") % 2 == 0, f"{p.name}: '--' or quote problem in: {ln}"
            for lit in re.findall(r"'[^']*'", code):
                assert ";" not in lit, f"{p.name}: ';' inside literal {lit}"


def test_prune_backups_keeps_the_newest_and_touches_nothing_else(tmp_path):
    from azfinops.migrations.runner import prune_backups
    db = tmp_path / "azfinops.duckdb"
    db.write_bytes(b"db")
    names = [f"azfinops.duckdb.bak-2026010{d}T000000Z" for d in range(1, 6)]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    keep_me = [tmp_path / "azfinops.duckdb.bak-notes.txt", tmp_path / "other.duckdb.bak-20260101T000000Z"]
    for p in keep_me:
        p.write_bytes(b"y")
    gone = prune_backups(db, keep=2)
    assert sorted(p.name for p in gone) == names[:3]
    assert sorted(p.name for p in tmp_path.iterdir() if ".bak-2026" in p.name and p.name.startswith("azfinops")) == names[3:]
    assert db.exists() and all(p.exists() for p in keep_me)


def test_integrity_migration_populates_scopes_and_marks_unbuilt_imports(tmp_path, monkeypatch):
    from azfinops.migrations import runner

    versions = discover()
    db = tmp_path / "old.duckdb"
    monkeypatch.setattr(runner, "discover", lambda: [v for v in versions if v[0] < 10])
    migrate(db)
    with duckdb.connect(str(db)) as con:
        con.execute("INSERT INTO import_batches (batch_id, source_kind, cost_kind, is_synthetic, status, "
                    "period_start, period_end, row_count, created_at, finished_at) VALUES "
                    "(1, 'csv', 'amortized', false, 'committed', DATE '2026-09-01', DATE '2026-09-30', 1, now(), now())")
        con.execute("INSERT INTO agg_daily (usage_date, usage_month, cost_kind, billing_account_id, billing_currency, "
                    "charge_type, application, environment, cost, payg_equivalent, cost_with_payg, payg_missing_rows, "
                    "contract_basis_rows, row_count, is_synthetic) VALUES "
                    "(DATE '2026-09-01', DATE '2026-09-01', 'amortized', 'a', 'GBP', 'Usage', 'x', 'e', 1, 1, 1, 0, 0, 1, false)")
    monkeypatch.setattr(runner, "discover", lambda: versions)
    assert migrate(db) == [10]
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT billing_account_id, days_present, period_complete "
                           "FROM aggregate_scope_freshness").fetchall() == [("a", 1, False)]
        assert con.execute("SELECT count(*) FROM aggregate_invalidations").fetchone()[0] == 1
    assert len(list(tmp_path.glob("old.duckdb.bak-*"))) == 1
