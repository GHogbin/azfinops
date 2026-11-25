"""Small settings the dashboard saves for the user: monthly budgets and saved Cost analysis views.

Kept as JSON files next to the database (like the exchange rates), so saving one never needs the single DuckDB
writer. Writes are atomic (temp file + replace) and serialised with a lock.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_QUERY_OK = re.compile(r"^[A-Za-z0-9=&%._,:+\-]{0,2000}$")
_SCOPE_OK = re.compile(r"^(all|[A-Za-z0-9_\-]{1,40})$")


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---- budgets: one monthly amount per scope ("all" or an enrollment id) and currency

def budgets(data_dir: Path) -> list[dict[str, Any]]:
    return _read(data_dir / "budgets.json")


def save_budget(data_dir: Path, scope: str, currency: str, amount: str | None) -> list[dict[str, Any]]:
    """Set (or, with amount None, remove) the monthly budget for a scope in a currency."""
    if not _SCOPE_OK.match(scope):
        raise ValueError("scope must be 'all' or an enrollment id")
    ccy = currency.upper()
    if not re.fullmatch(r"[A-Z]{3}", ccy):
        raise ValueError("currency must be a three-letter code")
    if amount is not None:
        try:
            v = Decimal(amount)
        except InvalidOperation as e:
            raise ValueError("amount must be a number") from e
        if not v.is_finite() or v <= 0 or v > Decimal("1e12"):
            raise ValueError("amount must be above zero")
    with _LOCK:
        rows = [r for r in budgets(data_dir) if not (r.get("scope") == scope and r.get("currency") == ccy)]
        if amount is not None:
            rows.append({"scope": scope, "currency": ccy, "amount": str(Decimal(amount)),
                         "updated": datetime.now().isoformat(timespec="seconds")})
        rows.sort(key=lambda r: (r["scope"], r["currency"]))
        _write(data_dir / "budgets.json", rows)
    return rows


# ---- saved Cost analysis views: a name and the URL query string that rebuilds the view

def views(data_dir: Path) -> list[dict[str, Any]]:
    return _read(data_dir / "views.json")


def save_view(data_dir: Path, name: str, query: str | None) -> list[dict[str, Any]]:
    """Save (or, with query None, delete) a named view. The query is the page's URL query string, nothing else."""
    name = name.strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("name must be 1 to 60 characters")
    q = None if query is None else query.lstrip("?")
    if q is not None and not _QUERY_OK.match(q):
        raise ValueError("query contains characters a dashboard URL does not use")
    with _LOCK:
        rows = [r for r in views(data_dir) if r.get("name") != name]
        if q is not None:
            if len(rows) >= 100:
                raise ValueError("at most 100 saved views")
            rows.append({"name": name, "query": q, "saved": datetime.now().isoformat(timespec="seconds")})
        rows.sort(key=lambda r: r["name"].lower())
        _write(data_dir / "views.json", rows)
    return rows
