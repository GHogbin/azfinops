"""Versioned, checksummed, forward-only migrations for the DuckDB database.

Safety: before applying pending migrations to an EXISTING database file, a timestamped copy is
written next to it (`<db>.bak-<UTC timestamp>`); only the newest two backups are kept. A previously applied migration whose file
checksum changed aborts the run. Each migration runs in its own transaction.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb

MIGRATIONS_DIR = Path(__file__).parent


def _statements(sql: str) -> list[str]:
    # Migrations must not contain string literals with '--' or ';' (checked by tests).
    lines = [ln.split("--", 1)[0] for ln in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _requires_empty(path: Path) -> list[str]:
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("-- requires-empty:"):
            name = ln.split(":", 1)[1].strip()
            if not name.isidentifier():
                raise ValueError(f"bad table name in {path.name}: {name!r}")
            out.append(name)
    return out


def discover() -> list[tuple[int, str, Path]]:
    out = []
    for p in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        out.append((int(p.name[:4]), p.stem[5:], p))
    return out


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


KEEP_BACKUPS = 2


def prune_backups(db_path: Path, keep: int = KEEP_BACKUPS) -> list[Path]:
    """Delete all but the newest `keep` backups of this database. Returns what was deleted.

    Only files named exactly `<db name>.bak-<UTC timestamp>` next to the database are touched. The names sort
    chronologically, so the newest are the last ones.
    """
    pat = re.compile(re.escape(db_path.name) + r"\.bak-\d{8}T\d{6}Z$")
    found = sorted(p for p in db_path.parent.iterdir() if p.is_file() and pat.fullmatch(p.name))
    doomed = found[:-keep] if keep > 0 else found
    for p in doomed:
        p.unlink()
    return doomed


def backup_database(db_path: Path, *, prune: bool = True) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    if target.exists():
        raise FileExistsError(f"backup already exists: {target}. Retry in a moment.")
    try:
        shutil.copy2(db_path, target)
    except OSError:
        target.unlink(missing_ok=True)
        raise
    if prune:
        prune_backups(db_path)
    return target


def migrate(db_path: Path, backup: bool = True) -> list[int]:
    """Apply pending migrations; returns the versions applied."""
    existed = db_path.exists()
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
                    "checksum VARCHAR NOT NULL, applied_at TIMESTAMP NOT NULL)")
        applied = {v: c for v, c in con.execute(
            "SELECT version, checksum FROM schema_migrations").fetchall()}
        pending = []
        for version, name, path in discover():
            if version in applied:
                if applied[version] != checksum(path):
                    raise RuntimeError(f"migration {version} changed after being applied")
            else:
                pending.append((version, name, path))
        if pending and existed and backup and applied:
            con.close()
            backup_database(db_path)
            con = duckdb.connect(str(db_path))
        done = []
        for version, name, path in pending:
            for table in _requires_empty(path):
                exists = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                                     [table]).fetchall()[0][0]
                if exists and con.execute(f'SELECT count(*) FROM "{table}"').fetchall()[0][0]:
                    raise RuntimeError(f"migration {version} requires {table} to be empty")
            con.execute("BEGIN")
            try:
                for stmt in _statements(path.read_text(encoding="utf-8")):
                    con.execute(stmt)
                con.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                            [version, name, checksum(path), datetime.now(UTC).replace(tzinfo=None)])
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            done.append(version)
        return done
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply azfinops database migrations")
    ap.add_argument("db", type=Path, help="path to the .duckdb file (outside the repository)")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()
    applied = migrate(args.db, backup=not args.no_backup)
    print(f"applied: {applied}" if applied else "up to date")


if __name__ == "__main__":
    main()
