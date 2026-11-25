"""Streaming CSV importer (one batch = one or more partition files of the same export).

Design
- Files are read in Arrow blocks (never loaded whole); every column is read as text and typed in SQL
  (see mapping.build_typed_sql) so bad values are rejected per row with an actionable reason.
- Atomicity is batch-level: rows are written while the batch is 'running' and are invisible (all
  queries use v_cost_facts) until a final small transaction flips the batch to 'committed'. Any
  failure deletes the batch's rows and marks it 'failed'. A crashed run is cleaned up on next start.
- Identical rows inside a file are legitimate billing rows and are kept. Re-imports are caught by
  file sha256; overlapping (cost kind, account, period) batches are rejected unless supersede=True.
- This module is NOT exercised by the author's sandbox (no DuckDB there); see tests/test_importer.py.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv

from .. import retention
from ..canonical import COST_KINDS
from ..config import Settings
from .mapping import FACT_COLS, ColumnMap, MappingError, build_typed_sql, clean, detect

BLOCK_BYTES = 64 * 1024 * 1024
Progress = Callable[[dict[str, Any]], None]


class ImportRejected(Exception):
    """The import was refused or failed; nothing from it is visible."""


@dataclass
class ImportResult:
    batch_id: int
    rows_loaded: int
    rows_rejected: int
    files: int
    period_start: date | None
    period_end: date | None
    warnings: dict[str, int] = field(default_factory=dict)
    unmapped_columns: list[str] = field(default_factory=list)
    superseded_batches: list[int] = field(default_factory=list)
    elapsed_s: float = 0.0
    retention: retention.RetentionPlan | None = None

    @property
    def rows_retained(self) -> int:
        return self.rows_loaded - (self.retention.rows_by_batch.get(self.batch_id, 0) if self.retention else 0)


def _one(con: duckdb.DuckDBPyConnection, sql: str,
         params: list[Any] | None = None) -> tuple[Any, ...] | None:
    """First row (or None), reading the whole result so the connection is free for the next call.
    (fetchone() leaves the result open and DuckDB then refuses unregister()/other work.)"""
    rows = (con.execute(sql, params) if params else con.execute(sql)).fetchall()
    return rows[0] if rows else None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def sha256_file(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _check_path(p: Path, allowed_dirs: tuple[Path, ...] | None, max_bytes: int | None) -> Path:
    rp = p.resolve(strict=True)
    if rp.suffix.lower() != ".csv":
        raise ImportRejected(f"{p.name}: only .csv files are supported")
    if allowed_dirs is not None:
        roots = [d.resolve() for d in allowed_dirs]
        if not any(rp.is_relative_to(r) for r in roots):
            raise ImportRejected(f"{p.name}: not inside an allowed import folder ({', '.join(map(str, roots))})")
    if max_bytes is not None and rp.stat().st_size > max_bytes:
        raise ImportRejected(f"{p.name}: larger than the configured limit of {max_bytes} bytes")
    return rp


def _open(path: Path, names: list[str] | None = None) -> pacsv.CSVStreamingReader:
    conv = None
    if names is not None:
        conv = pacsv.ConvertOptions(column_types={n: pa.string() for n in names}, null_values=[""],
                                    strings_can_be_null=False)
    return pacsv.open_csv(path, read_options=pacsv.ReadOptions(block_size=BLOCK_BYTES),
                          convert_options=conv)


def import_files(db_path: Path, csv_paths: list[Path], cost_kind: str, *, synthetic: bool = False,
                 supersede: bool = False, allowed_dirs: tuple[Path, ...] | None = None,
                 max_bytes: int | None = None, max_reject_ratio: float = 0.001,
                 max_stored_errors: int = 1000, settings: Settings | None = None,
                 progress: Progress | None = None, reimport: bool = False) -> ImportResult:
    if cost_kind not in COST_KINDS:
        raise ImportRejected(f"cost_kind must be one of {sorted(COST_KINDS)}")
    if not csv_paths:
        raise ImportRejected("no files given")
    t0 = time.monotonic()
    paths = [_check_path(Path(p), allowed_dirs, max_bytes) for p in csv_paths]
    if len(set(paths)) != len(paths):
        raise ImportRejected("the same file was given twice")

    con = duckdb.connect(str(db_path))
    try:
        if settings is not None:
            settings.apply(con)
        _recover_stale(con)
        hashes = [sha256_file(p) for p in paths]
        for p, (sha, _) in zip(paths, hashes, strict=True):
            row = _one(con, "SELECT b.batch_id FROM import_files f JOIN import_batches b USING (batch_id) "
                              "WHERE f.sha256 = ? AND b.status = 'committed'", [sha])
            if row and not (reimport and supersede):
                raise ImportRejected(f"{p.name}: identical file already imported as batch {row[0]}")
        batch_id = (_one(con, "SELECT COALESCE(max(batch_id), 0) + 1 FROM import_batches") or [1])[0]
        file_id0 = (_one(con, "SELECT COALESCE(max(file_id), 0) FROM import_files") or [0])[0]
        con.execute("INSERT INTO import_batches (batch_id, source_kind, cost_kind, is_synthetic, status, "
                    "created_at) VALUES (?, 'csv', ?, ?, 'running', ?)", [batch_id, cost_kind, synthetic, _now()])
        try:
            res = _load(con, batch_id, file_id0, paths, hashes, cost_kind, synthetic, max_stored_errors, progress,
                        use_payg=(settings or Settings.from_env()).use_payg_price)
            total = res.rows_loaded + res.rows_rejected
            if total == 0:
                raise ImportRejected("the file(s) contain no data rows")
            if res.rows_loaded == 0:
                raise ImportRejected("the file(s) contain no valid data rows")
            if res.rows_rejected / total > max_reject_ratio:
                raise ImportRejected(
                    f"{res.rows_rejected} of {total} rows were invalid (limit {max_reject_ratio:.2%}); "
                    f"see validation_results for batch {batch_id}")
            res.superseded_batches = _commit(con, batch_id, cost_kind, supersede, res, progress)
        except BaseException as e:
            con.execute("DELETE FROM cost_facts WHERE batch_id = ?", [batch_id])
            con.execute("UPDATE import_batches SET status = 'failed', finished_at = ?, notes = ? "
                        "WHERE batch_id = ?", [_now(), str(e)[:1000], batch_id])
            raise
        res.elapsed_s = time.monotonic() - t0
        return res
    finally:
        con.close()


def _recover_stale(con: duckdb.DuckDBPyConnection) -> None:
    """Single-writer assumption: any 'running' batch at start belongs to a crashed run."""
    con.execute("DELETE FROM cost_facts WHERE batch_id IN (SELECT batch_id FROM import_batches "
                "WHERE status = 'running')")
    con.execute("UPDATE import_batches SET status = 'failed', finished_at = ?, "
                "notes = 'interrupted; cleaned up on next start' WHERE status = 'running'", [_now()])


def _load(con: duckdb.DuckDBPyConnection, batch_id: int, file_id0: int, paths: list[Path],
          hashes: list[tuple[str, int]], cost_kind: str, synthetic: bool, max_stored_errors: int,
          progress: Progress | None, use_payg: bool = True) -> ImportResult:
    loaded = rejected = stored_errors = 0
    warnings: dict[str, int] = {}
    cm: ColumnMap | None = None
    first_header: list[str] | None = None
    typed_sql = ""
    cols = ", ".join(FACT_COLS)
    for i, (path, (sha, size)) in enumerate(zip(paths, hashes, strict=True)):
        file_id = file_id0 + i + 1
        names = list(_open(path).schema.names)
        if cm is None:
            try:
                cm = detect(names, use_payg)
            except MappingError as e:
                raise ImportRejected(f"{path.name}: {e}") from e
            first_header, typed_sql = names, build_typed_sql(cm, synthetic)
        elif [clean(n) for n in names] != [clean(n) for n in first_header or []]:
            raise ImportRejected(f"{path.name}: header differs from the first file; partitions of one "
                                 "export must share a header")
        file_rows = 0
        offset = 0
        reader = _open(path, names)
        for block in reader:
            tbl = pa.Table.from_batches([block]).append_column(
                "_rn", pa.array(range(offset + 1, offset + block.num_rows + 1), type=pa.int64()))
            offset += block.num_rows
            con.register("_raw", tbl)
            con.execute(typed_sql)
            ok, bad = con.execute("SELECT count(*) FILTER (WHERE reject_code IS NULL), "
                                  "count(*) FILTER (WHERE reject_code IS NOT NULL) FROM _typed").fetchall()[0]
            con.execute(f"INSERT INTO cost_facts (batch_id, file_id, row_number, cost_kind, usage_month, {cols}) "
                        f"SELECT {batch_id}, {file_id}, _rn, '{cost_kind}', "
                        f"CAST(date_trunc('month', usage_date) AS DATE), {cols} FROM _typed "
                        "WHERE reject_code IS NULL")
            if bad and stored_errors < max_stored_errors:
                rows = con.execute("SELECT _rn, reject_col, reject_code, reject_val FROM _typed "
                                   "WHERE reject_code IS NOT NULL ORDER BY _rn LIMIT ?",
                                   [max_stored_errors - stored_errors]).fetchall()
                con.executemany(
                    "INSERT INTO validation_results (batch_id, file_id, severity, code, row_number, "
                    "column_name, message, sample_value) VALUES (?, ?, 'error', ?, ?, ?, ?, ?)",
                    [(batch_id, file_id, code, rn, col, f"row rejected: {code.replace('_', ' ')}",
                      (val or "")[:100]) for rn, col, code, val in rows])
                stored_errors += len(rows)
            kind_bad = ("charge_type IN ('Purchase','Refund') AND cost <> 0" if cost_kind == "amortized"
                        else "charge_type LIKE 'Unused%'")
            checks = [("kind_mismatch", f"reject_code IS NULL AND {kind_bad}"),
                      ("unparseable_tags", "reject_code IS NULL AND bad_tags")]
            if not cm.contract_baseline:      # with the contract baseline every priced row is 'contract' by design
                checks.append(("contract_basis_payg", "reject_code IS NULL AND payg_basis = 'contract'"))
            for label, cond in checks:
                n = con.execute(f"SELECT count(*) FROM _typed WHERE {cond}").fetchall()[0][0]
                if n:
                    warnings[label] = warnings.get(label, 0) + n
            con.unregister("_raw")
            loaded += ok
            rejected += bad
            file_rows += ok + bad
            if progress:
                progress({"file": path.name, "file_index": i + 1, "files": len(paths),
                          "rows_loaded": loaded, "rows_rejected": rejected})
        con.execute("INSERT INTO import_files (file_id, batch_id, file_name, sha256, byte_count, row_count, "
                    "schema_id, partition_ordinal) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [file_id, batch_id, path.name, sha, size, file_rows,
                     f"auto:{cm.n_mapped}/{len(names)}", i + 1])
    for code, n in warnings.items():
        msg = {"kind_mismatch": (f"{n} rows have charge types that do not belong in a {cost_kind} export "
                         "(non-zero Purchase/Refund in amortized, or Unused* in actual)"),
               "unparseable_tags": f"{n} rows had Tags that are not valid JSON key/value text; tags dropped",
               "contract_basis_payg": f"{n} reservation/savings-plan rows lack PayGPrice; valued at UnitPrice "
                                      "(contract basis) for coverage"}[code]
        con.execute("INSERT INTO validation_results (batch_id, severity, code, message) VALUES (?, 'warning', ?, ?)",
                    [batch_id, code, msg])
    assert cm is not None
    return ImportResult(batch_id, loaded, rejected, len(paths), None, None, warnings, cm.unmapped)


def _commit(con: duckdb.DuckDBPyConnection, batch_id: int, cost_kind: str,
            supersede: bool, res: ImportResult, progress: Progress | None = None) -> list[int]:
    bounds = _one(con, "SELECT min(usage_date), max(usage_date) FROM cost_facts WHERE batch_id = ?", [batch_id])
    if bounds is None or not all(isinstance(d, date) for d in bounds):
        raise ImportRejected("the batch contains no valid usage dates")
    lo, hi = bounds
    res.period_start, res.period_end = lo, hi
    try:
        _latest, cutoff = retention.window(con, hi)
    except ValueError as e:
        raise ImportRejected(str(e)) from e
    if cutoff is not None and hi < cutoff:
        raise ImportRejected(f"all usage dates are before the retained window starting {cutoff:%Y-%m}; nothing imported")
    overlapping = [r[0] for r in con.execute(
        "SELECT batch_id FROM import_batches WHERE status = 'committed' AND cost_kind = ? "
        "AND period_start <= ? AND period_end >= ?", [cost_kind, hi, lo]).fetchall()]
    clash: list[int] = []
    if overlapping:
        ph = ",".join("?" * len(overlapping))
        clash = [r[0] for r in con.execute(
            "WITH incoming AS (SELECT billing_account_id, min(usage_date) AS lo, max(usage_date) AS hi "
            "FROM cost_facts WHERE batch_id = ? GROUP BY billing_account_id) "
            "SELECT DISTINCT old.batch_id FROM cost_facts old JOIN incoming new "
            "ON old.billing_account_id = new.billing_account_id AND old.usage_date BETWEEN new.lo AND new.hi "
            f"WHERE old.batch_id IN ({ph})", [batch_id, *overlapping]).fetchall()]
    if clash and not supersede:
        raise ImportRejected(f"overlaps committed batch(es) {clash} for the same cost kind, account and "
                             "period; re-run with supersede to replace them")
    if clash:
        ph = ",".join("?" * len(clash))
        narrower = con.execute(
            "WITH incoming AS (SELECT DISTINCT billing_account_id, billing_currency, usage_date "
            "FROM cost_facts WHERE batch_id = ?), previous AS ("
            "SELECT DISTINCT batch_id, billing_account_id, billing_currency, usage_date FROM cost_facts "
            f"WHERE batch_id IN ({ph})) "
            "SELECT DISTINCT batch_id FROM previous ANTI JOIN incoming "
            "USING (billing_account_id, billing_currency, usage_date) ORDER BY 1",
            [batch_id, *clash]).fetchall()
        if narrower:
            raise ImportRejected(
                f"replacement is narrower than committed batch(es) {[r[0] for r in narrower]}; "
                "include every previously loaded enrollment, currency and date, and all export partitions. "
                "Nothing was replaced")
    con.execute("BEGIN")
    try:
        for old in clash:
            con.execute("UPDATE import_batches SET status = 'superseded', finished_at = ? WHERE batch_id = ?",
                        [_now(), old])
        con.execute("UPDATE import_batches SET status = 'committed', finished_at = ?, period_start = ?, "
                    "period_end = ?, row_count = ?, rejected_row_count = ?, supersedes_batch_id = ? "
                    "WHERE batch_id = ?",
                    [_now(), lo, hi, res.rows_loaded, res.rows_rejected, clash[0] if clash else None, batch_id])
        if progress:
            progress({"phase": "retention", "file": "12-month retention", "rows_loaded": res.rows_loaded,
                      "rows_rejected": res.rows_rejected})
        res.retention = retention.plan(con)
        retention.purge(con, res.retention)
        ids = [batch_id, *clash]
        con.execute(
            "INSERT OR REPLACE INTO aggregate_invalidations "
            "SELECT DISTINCT usage_month, ?, CAST(now() AS TIMESTAMP), ? FROM cost_facts "
            f"WHERE batch_id IN ({','.join('?' * len(ids))})", [cost_kind, batch_id, *ids])
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return clash
