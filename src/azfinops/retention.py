"""Portfolio-wide rolling retention, applied inside a successful cost-import transaction."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from .config import Settings
from .migrations.runner import backup_database

MONTHS = 12
AUDIT_CODE = "retention_purged_rows"
DERIVED_TABLES = (
    "agg_daily", "agg_resource_month", "aggregate_freshness",
    "aggregate_scope_freshness", "aggregate_invalidations",
)


@dataclass(frozen=True)
class RetentionPlan:
    newest_month: date | None
    cutoff: date | None
    rows_by_batch: dict[int, int] = field(default_factory=dict)
    derived_rows: int = 0

    @property
    def fact_rows(self) -> int:
        return sum(self.rows_by_batch.values())

    @property
    def has_changes(self) -> bool:
        return bool(self.fact_rows or self.derived_rows)

    def describe(self) -> str:
        if self.cutoff is None or self.newest_month is None:
            return "12-month retention: no committed cost data."
        return (f"12-month window {self.cutoff:%Y-%m}..{self.newest_month:%Y-%m}; "
                f"{self.fact_rows:,} expired stored cost rows and {self.derived_rows:,} derived rows")


def window(con: duckdb.DuckDBPyConnection, incoming_latest: date | None = None) -> tuple[date | None, date | None]:
    saved = con.execute(
        "SELECT max(period_end) FROM import_batches WHERE status = 'committed' "
        "AND cost_kind IN ('actual', 'amortized')").fetchall()[0][0]
    latest = max((d for d in (saved, incoming_latest) if d is not None), default=None)
    if latest is None:
        return None, None
    latest = latest.replace(day=1)
    if latest > datetime.now(UTC).date().replace(day=1):
        raise ValueError(f"Latest cost month {latest:%Y-%m} is in the future. Correct the dates before retention.")
    index = max(0, (latest.year - 1) * 12 + latest.month - MONTHS)
    year, month = divmod(index, 12)
    return latest, date(year + 1, month + 1, 1)


def plan(con: duckdb.DuckDBPyConnection) -> RetentionPlan:
    latest, cutoff = window(con)
    if cutoff is None:
        return RetentionPlan(latest, cutoff)
    batches = {int(batch): int(count) for batch, count in con.execute(
        "SELECT batch_id, count(*) FROM cost_facts WHERE usage_month < ? GROUP BY batch_id", [cutoff]).fetchall()}
    derived = sum(int(con.execute(
        f"SELECT count(*) FROM {table} WHERE usage_month < ?", [cutoff]).fetchall()[0][0])
        for table in DERIVED_TABLES)
    return RetentionPlan(latest, cutoff, batches, derived)


def purge(con: duckdb.DuckDBPyConnection, candidate: RetentionPlan) -> None:
    """The caller owns the transaction, including audit entries and derived-data cleanup."""
    if candidate.cutoff is None:
        return
    deleted = int(con.execute(
        "DELETE FROM cost_facts WHERE usage_month < ?", [candidate.cutoff]).fetchall()[0][0])
    if deleted != candidate.fact_rows:
        raise RuntimeError("Retention counts changed before deletion; roll back and retry.")
    for table in DERIVED_TABLES:
        con.execute(f"DELETE FROM {table} WHERE usage_month < ?", [candidate.cutoff])
    for batch_id, removed in candidate.rows_by_batch.items():
        previous = con.execute(
            "SELECT sample_value FROM validation_results WHERE batch_id = ? AND code = ?",
            [batch_id, AUDIT_CODE]).fetchall()
        cumulative = removed + sum(int(row[0]) for row in previous)
        con.execute("DELETE FROM validation_results WHERE batch_id = ? AND code = ?", [batch_id, AUDIT_CODE])
        con.execute(
            "INSERT INTO validation_results (batch_id, severity, code, column_name, message, sample_value) "
            "VALUES (?, 'info', ?, 'usage_month', ?, ?)",
            [batch_id, AUDIT_CODE,
             (f"Rolling 12-month retention has purged {cumulative:,} stored cost rows before "
              f"{candidate.cutoff:%Y-%m}. Accepted/rejected file counts remain the original import history."),
             str(cumulative)])


def apply_existing(db_path: Path, settings: Settings | None = None) -> RetentionPlan:
    """Back up first, then atomically purge existing data with all dashboard readers stopped."""
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    con = duckdb.connect(str(db_path))
    try:
        if settings is not None:
            settings.apply(con)
        if con.execute("SELECT count(*) FROM import_batches WHERE status = 'running'").fetchall()[0][0]:
            raise RuntimeError("An interrupted import needs recovery before applying retention.")
        candidate = plan(con)
        if not candidate.has_changes:
            return candidate
        con.close()
        backup_database(db_path, prune=False)
        con = duckdb.connect(str(db_path))
        if settings is not None:
            settings.apply(con)
        con.execute("BEGIN")
        try:
            candidate = plan(con)
            purge(con, candidate)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        return candidate
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or apply the shared latest-12-month cost retention window")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--apply", action="store_true", help="stop the dashboard first; backs up before deleting")
    args = parser.parse_args()
    settings = Settings.from_env()
    path = args.db or settings.db_path
    if not path.is_file():
        raise SystemExit(f"database not found: {path}")
    if args.apply:
        candidate = apply_existing(path, settings)
        print(f"Retention applied: {candidate.describe()}. CSV files, price sheets, settings and rates are unchanged.")
    else:
        with duckdb.connect(str(path), read_only=True) as con:
            settings.apply(con)
            candidate = plan(con)
        print(f"Preview only (no changes): {candidate.describe()}. Only expired rows would be removed with --apply.")


if __name__ == "__main__":
    main()
