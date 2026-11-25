"""Imports started from the dashboard (Data Management): one job at a time, run in a background thread.

The server's read-only connection is paused for the whole job, because DuckDB allows one writer and no readers while
it writes. Files can only come from the allowed import folders (Settings.allowed_import_dirs) and are named by
folder number and path inside it, never by a path the browser sends. Usage files whose names differ only by a
trailing _<number> are taken as the partitions of one export and imported as one batch; every other file is its own
batch. Each usage batch is followed by the same steps as the command line: aggregates for the months it touched,
then subscription and enrollment names.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .config import Settings
from .ingestion.importer import ImportRejected
from .pricesheet import PriceSheetError

KINDS = ("usage", "pricesheet")
_PART = re.compile(r"^(?P<stem>.+?)_(?P<n>\d+)$")


def guess_kind(name: str) -> str | None:
    n = name.lower()
    if "pricesheet" in n or "price_sheet" in n:
        return "pricesheet"
    if n.startswith(("detail", "usage", "cost", "amortized", "actual")) or "usage" in n or "charges" in n:
        return "usage"
    return None


def list_files(settings: Settings, imported_names: set[str] | None = None) -> dict[str, Any]:
    """CSV files in the allowed import folders (and one level of sub-folders), newest first."""
    folders = list(settings.allowed_import_dirs)
    out = []
    for i, root in enumerate(folders):
        if root == settings.data_dir / "imports":
            try:
                root.mkdir(parents=True, exist_ok=True)       # the default drop folder, created on first look
            except OSError:
                pass
        if not root.is_dir():
            continue
        for p in sorted([*root.glob("*.csv"), *root.glob("*/*.csv")]):
            try:
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            out.append({"id": f"{i}/{rel}", "folder": str(root), "name": rel, "bytes": st.st_size,
                        "modified": datetime.fromtimestamp(st.st_mtime, UTC).replace(tzinfo=None),
                        "kind": guess_kind(p.name),
                        "imported": None if imported_names is None else p.name in imported_names})
    out.sort(key=lambda x: (x["modified"], x["name"]), reverse=True)
    return {"folders": [str(f) for f in folders], "files": out}


def resolve(settings: Settings, file_id: str) -> Path:
    """Turn a file id from list_files back into a path, refusing anything outside the allowed folders."""
    folders = list(settings.allowed_import_dirs)
    idx, _, rel = file_id.partition("/")
    if not idx.isdigit() or int(idx) >= len(folders) or not rel or "\\" in rel or rel.startswith("/"):
        raise ValueError(f"unknown file {file_id!r}")
    root = folders[int(idx)].resolve()
    p = (root / rel).resolve()
    if not p.is_relative_to(root) or p.suffix.lower() != ".csv" or not p.is_file():
        raise ValueError(f"unknown file {file_id!r}")
    return p


def group_parts(paths: list[Path]) -> list[list[Path]]:
    """Partitions of one export (same name apart from a trailing _<number>) form one batch; others stand alone."""
    groups: dict[str, list[Path]] = {}
    for p in paths:
        m = _PART.match(p.stem)
        key = str(p.parent / m.group("stem")) if m else str(p)
        groups.setdefault(key, []).append(p)
    return [sorted(g) for _k, g in sorted(groups.items())]


class ImportJobs:
    """Holds the one running (or last finished) dashboard import and its progress."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status: dict[str, Any] = {"state": "idle"}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {**self.status, "log": list(self.status.get("log", []))}

    def _set(self, **kw: Any) -> None:
        with self._lock:
            self.status.update(kw)

    def _log(self, line: str) -> None:
        with self._lock:
            self.status.setdefault("log", []).append(f"{datetime.now():%H:%M:%S} {line}")

    def start(self, db: Any, settings: Settings, kind: str, paths: list[Path], *, cost_kind: str = "amortized",
              supersede: bool = False, reimport: bool = False) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if not paths:
            raise ValueError("choose at least one file")
        with self._lock:
            if self.status.get("state") == "running":
                raise RuntimeError("an import is already running")
            self.status = {"state": "running", "kind": kind, "files": [p.name for p in paths], "step": "starting",
                           "current": None, "rows_loaded": 0, "rows_rejected": 0, "started": datetime.now(),
                           "finished": None, "error": None, "results": [], "log": [], "data_committed": False}
        t = threading.Thread(target=self._run, name="azfinops-import", daemon=True,
                             args=(db, settings, kind, paths, cost_kind, supersede, reimport))
        t.start()
        return self.snapshot()

    def _run(self, db: Any, settings: Settings, kind: str, paths: list[Path], cost_kind: str,
             supersede: bool, reimport: bool) -> None:
        t0 = time.monotonic()
        paused = False
        outcome: dict[str, Any] = {}
        try:
            self._set(step="waiting for running queries to finish")
            db.pause()
            paused = True
            from .migrations.runner import migrate
            self._set(step="checking the database")
            applied = migrate(db.path)
            if applied:
                self._log(f"applied migrations: {applied}")
            if kind == "pricesheet":
                self._prices(db.path, paths)
            else:
                self._usage(db.path, settings, paths, cost_kind, supersede, reimport)
            self._log(f"finished in {time.monotonic() - t0:.0f}s")
            outcome = {"state": "done", "step": "finished"}
        except (ImportRejected, PriceSheetError, OSError, ValueError, RuntimeError, duckdb.Error) as e:
            self._log(f"failed: {e}")
            outcome = {"state": "failed", "step": "failed", "error": f"{type(e).__name__}: {e}"}
        finally:
            # Reopen the dashboard before saying the job is over, so a caller who sees done or failed can use it.
            if paused:
                self._set(step="reopening the dashboard")
                try:
                    db.resume()
                except (OSError, ValueError, RuntimeError, duckdb.Error) as e:
                    outcome = {"state": "failed", "step": "failed",
                               "error": f"could not reopen the database: {e}. Restart azfinops-serve."}
            self._set(**(outcome or {"state": "failed", "step": "failed", "error": "stopped"}),
                      recovery_required=db.recovery_required, finished=datetime.now())

    def _progress(self, p: dict[str, Any]) -> None:
        self._set(current=p.get("file"), rows_loaded=int(p.get("rows_loaded", p.get("loaded", 0)) or 0),
                  rows_rejected=int(p.get("rows_rejected", p.get("rejected", 0)) or 0))
        if p.get("phase") == "retention":
            self._set(step="applying portfolio-wide 12-month retention")

    def _prices(self, db_path: Path, paths: list[Path]) -> None:
        from .pricesheet import import_price_sheets
        self._set(step=f"importing {len(paths)} price sheet file(s)")
        r = import_price_sheets(db_path, paths, progress=self._progress)
        self._set(data_committed=True)
        summary = (f"price sheet: {r.rows_loaded:,} rows from {r.files} file(s), {r.rows_rejected:,} rejected; "
                   f"periods {', '.join(r.periods) or 'none'}"
                   + (f"; replaced {r.replaced_rows:,} rows already loaded" if r.replaced_rows else ""))
        self._log(summary)
        with self._lock:
            self.status["results"].append({"files": [p.name for p in paths], "summary": summary,
                                           "rows_loaded": r.rows_loaded, "rows_rejected": r.rows_rejected})

    def _usage(self, db_path: Path, settings: Settings, paths: list[Path], cost_kind: str,
               supersede: bool, reimport: bool) -> None:
        from .aggregates import affected_months, rebuild
        from .ingestion.importer import import_files
        batches = group_parts(paths)
        for n, files in enumerate(batches, 1):
            label = files[0].name if len(files) == 1 else f"{files[0].name} and {len(files) - 1} more part(s)"
            self._set(step=f"importing batch {n} of {len(batches)}: {label}", rows_loaded=0, rows_rejected=0)
            r = import_files(db_path, files, cost_kind, supersede=supersede, reimport=reimport,
                             allowed_dirs=settings.allowed_import_dirs, settings=settings, progress=self._progress)
            self._set(data_committed=True)
            line = (f"batch {r.batch_id}: {r.rows_loaded:,} rows, {r.rows_rejected:,} rejected, "
                    f"{r.period_start}..{r.period_end}"
                    + (f", replaced batch(es) {', '.join(map(str, r.superseded_batches))}" if r.superseded_batches else ""))
            if r.retention:
                line += f"; {r.retention.describe()} purged; {r.rows_retained:,} rows from this batch retained"
            self._log(line)
            self._set(step=f"rebuilding the aggregates for batch {n} of {len(batches)}")
            con = duckdb.connect(str(db_path), read_only=True)
            try:
                months = affected_months(con, [r.batch_id, *r.superseded_batches])
            finally:
                con.close()
            done = rebuild(db_path, settings, months)
            self._log(f"aggregates rebuilt for {', '.join(m.isoformat()[:7] for m in done) or 'nothing'}")
            self._set(step=f"storing names for batch {n} of {len(batches)}")
            try:
                from .dimensions import harvest_and_store
                harvest_and_store(db_path, files)
                from .accounts import harvest_and_store as store_accounts
                store_accounts(settings.data_dir, files)
            except (OSError, ValueError, duckdb.Error) as e:
                self._log(f"warning: names not stored ({type(e).__name__}: {e})")
            for k, v in r.warnings.items():
                self._log(f"warning {k}: {v:,}")
            with self._lock:
                self.status["results"].append({"files": [p.name for p in files], "summary": line,
                                               "rows_loaded": r.rows_loaded, "rows_rejected": r.rows_rejected})
