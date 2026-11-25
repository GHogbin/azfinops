"""Read-only SQL workbench: validation and bounded execution.

Defence in depth, because the text is typed by a person and run inside the server process:
1. the database connection is opened read-only (writes fail regardless of what is typed);
2. `validate` accepts exactly one statement whose parsed type is SELECT (so no COPY, ATTACH, PRAGMA,
   SET, CALL, INSTALL, DDL or DML) and fails closed when the type cannot be determined;
3. the API sets enable_external_access=false and locks the configuration, so file readers such as
   read_csv or read_text, httpfs and ATTACH cannot reach the disk or the network;
4. results are capped (rows, columns, characters per cell) and the request has a deadline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import duckdb

MAX_SQL_CHARS = 20_000
MAX_ROWS = 1000
MAX_COLUMNS = 100
MAX_CELL_CHARS = 500


class SqlRejected(ValueError):
    """The statement is not allowed or is invalid. The message is safe to show to the person typing."""


@dataclass(frozen=True)
class Result:
    columns: list[str]
    types: list[str]
    rows: list[list[Any]]
    truncated: bool          # more rows existed than were returned
    limit: int


def validate(con: Any, sql: str) -> str:
    """Return the statement text to run, or raise SqlRejected."""
    text = (sql or "").strip()
    if not text:
        raise SqlRejected("Enter a query.")
    if len(text) > MAX_SQL_CHARS:
        raise SqlRejected(f"The query is longer than {MAX_SQL_CHARS:,} characters.")
    try:
        statements = con.extract_statements(text)
    except duckdb.Error as e:
        raise SqlRejected(f"Could not parse the query: {_first_line(e)}") from e
    if len(statements) == 0:
        raise SqlRejected("Enter a query.")
    if len(statements) > 1:
        raise SqlRejected("Run one statement at a time.")
    try:
        kind = statements[0].type
    except Exception as e:  # fail closed: if we cannot tell what it is, do not run it
        raise SqlRejected("Could not determine the statement type.") from e
    if kind != duckdb.StatementType.SELECT:
        name = getattr(kind, "name", str(kind))
        raise SqlRejected(f"Only SELECT queries are allowed here (this is {name}).")
    return text


def run(cur: Any, sql: str, limit: int = MAX_ROWS) -> Result:
    """Validate then execute `sql` on a cursor, returning at most `limit` rows."""
    limit = max(1, min(int(limit), MAX_ROWS))
    text = validate(cur, sql)
    try:
        cur.execute(text)
        description = cur.description or []
        if len(description) > MAX_COLUMNS:
            raise SqlRejected(f"The query returns {len(description)} columns; the limit is {MAX_COLUMNS}.")
        fetched = cur.fetchmany(limit + 1)
    except duckdb.InterruptException:
        raise                                   # the per-request deadline: the API maps it to 504
    except duckdb.Error as e:
        raise SqlRejected(_first_line(e)) from e
    truncated = len(fetched) > limit
    rows = [[_cell(v) for v in r] for r in fetched[:limit]]
    return Result([str(d[0]) for d in description], [str(d[1]) for d in description], rows, truncated, limit)


def _first_line(e: Exception) -> str:
    msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return msg[:300]


def _cell(v: Any) -> Any:
    """JSON-safe value. Decimals stay exact as strings; everything unusual becomes text."""
    if v is None or isinstance(v, bool | int):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, datetime | date | time):
        return v.isoformat()
    if isinstance(v, timedelta):
        return str(v)
    if isinstance(v, bytes | bytearray | memoryview):
        return f"<{len(bytes(v))} bytes>"
    s = str(v)
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "…"
