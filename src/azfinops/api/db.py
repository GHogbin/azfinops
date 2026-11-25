"""Read-only DuckDB access for the API: one root connection, one cursor per request.

* The database is opened read-only. Writers (import, aggregate rebuild) either run as separate processes with the
  server stopped, or through the dashboard's import job, which pauses this connection first (see pause/resume):
  DuckDB allows one writer and no readers while it writes.
* Each request gets its own cursor, a hard deadline (query_timeout_s) enforced with
  connection.interrupt(), and a slot in a bounded gate so a burst cannot exhaust memory.
* Time spent inside DuckDB is recorded per request for the Server-Timing header.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import duckdb

from ..config import Settings

# How long a request waits for one of the max_concurrent_queries slots before it is answered 503 "busy". Long enough
# for a screen that starts several heavy queries at once (Commitments: reservations, hourly cards, headroom per
# currency) to queue rather than fail; the browser also retries a busy answer a few times.
GATE_WAIT_S = 25.0
# Set per request by the timing middleware; the list is shared with worker threads.
request_timings: ContextVar[list[tuple[str, float]] | None] = ContextVar("request_timings", default=None)
log = logging.getLogger("azfinops.api.db")


class Overloaded(RuntimeError):
    """Too many concurrent queries; the caller should retry shortly."""


class QueryTimeout(RuntimeError):
    """The request exceeded query_timeout_s and its queries were interrupted."""


class Paused(RuntimeError):
    """The read-only connection is closed while an import writes to the database."""


class StaleAggregates(RuntimeError):
    """Committed imports have not yet been reflected in both aggregate tables."""


class Database:
    def __init__(self, path: Path, settings: Settings) -> None:
        if not Path(path).exists():
            raise FileNotFoundError(f"database not found: {path}. Import data first (azfinops-import).")
        self.settings = settings
        self.path = Path(path)
        self._paused = threading.Event()
        self._open()
        self._gate = threading.BoundedSemaphore(settings.max_concurrent_queries)

    def _open(self) -> None:
        self._root = duckdb.connect(str(self.path), read_only=True)
        try:
            tables = {r[0] for r in self._root.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()}
            if not {"aggregate_invalidations", "aggregate_scope_freshness"} <= tables:
                raise RuntimeError(
                    f"database schema is out of date. With the server stopped, run azfinops-migrate \"{self.path}\" "
                    "(a backup is made first), then restart the server")
            self.pending_aggregates = self._root.execute(
                "SELECT usage_month, cost_kind FROM aggregate_invalidations ORDER BY 1, 2").fetchall()
            self.settings.apply(self._root)
            from ..combined import register_decimal_conversion
            register_decimal_conversion(self._root)
            self.sql_enabled = self._lock_down()
        except BaseException:
            self._root.close()
            raise

    @property
    def recovery_required(self) -> bool:
        return bool(self.pending_aggregates)

    @property
    def recovery_message(self) -> str:
        months = ", ".join(f"{m:%Y-%m} ({kind})" for m, kind in self.pending_aggregates)
        return (f"Import data is committed but aggregate recovery is required for {months}. "
                "Cost screens are unavailable. Stop the server, run azfinops-aggregate, then restart it.")

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self, wait_s: float = 120.0) -> None:
        """Close the read-only connection so an import can write: new requests get Paused (HTTP 503) at once, and
        this waits for the queries already running. DuckDB allows one writer and no readers while it writes."""
        self._paused.set()
        taken = 0
        deadline = time.monotonic() + wait_s
        try:
            for _ in range(self.settings.max_concurrent_queries):
                if not self._gate.acquire(timeout=max(0.0, deadline - time.monotonic())):
                    raise Overloaded("queries still running; try the import again in a moment")
                taken += 1
            self._root.close()
        except BaseException:
            for _ in range(taken):
                self._gate.release()
            self._paused.clear()
            raise
        self._taken = taken

    def resume(self) -> None:
        """Reopen the read-only connection after an import and let requests through again."""
        try:
            self._open()
        finally:
            for _ in range(getattr(self, "_taken", 0)):
                self._gate.release()
            self._taken = 0
            self._paused.clear()

    def _lock_down(self) -> bool:
        """Block file and network access from SQL, then freeze the settings.

        Done for every request, not only the SQL workbench: nothing in the API needs the file system
        once the database is open. If DuckDB refuses, the workbench is switched off rather than run
        without the guard.
        """
        try:
            self._root.execute("SET enable_external_access=false")
            self._root.execute("SET lock_configuration=true")
            return True
        except duckdb.Error:
            return False

    def close(self) -> None:
        self._root.close()

    @contextmanager
    def cursor(self, timeout_s: float | None = None, f: Any = None, *,
               require_current: bool = True) -> Iterator[duckdb.DuckDBPyConnection]:
        if self._paused.is_set():
            raise Paused("an import is running; the dashboard is back when it finishes")
        if require_current and self.recovery_required:
            raise StaleAggregates(self.recovery_message)
        if not self._gate.acquire(timeout=GATE_WAIT_S):
            raise Overloaded("server is busy")
        try:
            cur = self._root.cursor()
        except BaseException:
            self._gate.release()
            raise
        timed_out = threading.Event()

        def fire() -> None:
            timed_out.set()
            try:
                cur.interrupt()
            except duckdb.Error:
                log.debug("deadline cursor already closed", exc_info=True)

        limit = self.settings.query_timeout_s if timeout_s is None else min(timeout_s, self.settings.query_timeout_s)
        timer = threading.Timer(limit, fire)
        timer.daemon = True
        timer.start()
        t0 = time.perf_counter()
        try:
            if f is not None and getattr(f, "combined", False):
                from .. import combined, fx
                combined.install(cur, f.currency, fx.load(self.settings.data_dir), f)
            yield cur
        except duckdb.InterruptException as e:
            if timed_out.is_set():
                raise QueryTimeout(f"query exceeded {limit:g}s") from e
            raise
        finally:
            timer.cancel()
            sink = request_timings.get()
            if sink is not None:
                sink.append(("db", (time.perf_counter() - t0) * 1000))
            try:
                cur.close()
            finally:
                self._gate.release()
