"""Exchange rates: derived from the Azure Retail Prices API (the public price list behind the Azure Pricing
Calculator) and stored in a small JSON file next to the database.

Why not DuckDB: the dashboard holds a read-only database connection and there is a single writer, so a rate saved
from the dashboard goes to its own file and cannot collide with an import.

How a rate is derived: the same price list is requested in two currencies. Each item that appears in both (matched on
meterId, skuId and unit of measure) gives one ratio, to / from. The reported rate is the median of those ratios; the
spread (min and max) is reported so an odd result is visible. This gives the rate Microsoft currently uses for list
prices. It is not the rate on any invoice, and the price list only offers today's rate, so a past month has to be
entered by hand. This module finds, stores and resolves dated rates; conversion consumers apply them
to money separately. The standard-library resolver is shared by the main app and standalone reports.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import Path
from statistics import median
from typing import Any

RETAIL_URL = "https://prices.azure.com/api/retail/prices"
# A broad, stable slice of the price list; the filter is fixed, never taken from a request.
REFERENCE_FILTER = "serviceName eq 'Virtual Machines' and armRegionName eq 'uksouth' and priceType eq 'Consumption'"
MAX_BYTES = 8_000_000
MIN_MATCHES = 20
CCY = re.compile(r"^[A-Z]{3}$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
RATE_QUANTUM = Decimal("0.000000000000000001")
_lock = threading.Lock()


class FxError(ValueError):
    """The request was understood but no usable rate could be produced (maps to HTTP 422)."""


class FxUnavailable(RuntimeError):
    """The price list could not be reached (maps to HTTP 502)."""


Fetch = Callable[[str], dict[str, Any]]


def http_get(url: str) -> dict[str, Any]:
    if not url.startswith(RETAIL_URL + "?"):
        raise FxUnavailable("refusing to fetch an unexpected address")
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "azfinops-local"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FxUnavailable(f"could not reach the Azure price list: {e}") from e
    if len(raw) > MAX_BYTES:
        raise FxUnavailable("the price list response was too large")
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except ValueError as e:
        raise FxUnavailable("the price list returned something that is not JSON") from e


def _url(currency: str) -> str:
    q = urllib.parse.urlencode({"currencyCode": f"'{currency}'", "$filter": REFERENCE_FILTER}, quote_via=urllib.parse.quote)
    return f"{RETAIL_URL}?{q}"


def _prices(fetch: Fetch, currency: str) -> dict[tuple[str, str, str], Decimal]:
    body = fetch(_url(currency))
    out: dict[tuple[str, str, str], Decimal] = {}
    for it in body.get("Items") or []:
        try:
            p = Decimal(str(it["retailPrice"]))
            key = (str(it["meterId"]), str(it.get("skuId", "")), str(it.get("unitOfMeasure", "")))
        except (KeyError, InvalidOperation):
            continue
        if p > 0 and it.get("type", "Consumption") == "Consumption":
            out[key] = p
    return out


def implied_rate(from_ccy: str, to_ccy: str, fetch: Fetch = http_get) -> dict[str, Any]:
    if not CCY.match(from_ccy) or not CCY.match(to_ccy):
        raise FxError("currencies must be three upper-case letters, such as USD and GBP")
    if from_ccy == to_ccy:
        raise FxError("choose two different currencies")
    a, b = _prices(fetch, from_ccy), _prices(fetch, to_ccy)
    ratios = sorted(b[k] / a[k] for k in a.keys() & b.keys())
    if len(ratios) < MIN_MATCHES:
        raise FxError(f"only {len(ratios)} price-list items matched in both currencies (need {MIN_MATCHES}); "
                      "the currency may not be offered by the price list")
    rate = median(ratios)
    return {"from": from_ccy, "to": to_ccy, "rate": format(rate.quantize(Decimal("0.000001")), "f"),
            "matched": len(ratios), "min": format(ratios[0].quantize(Decimal("0.000001")), "f"),
            "max": format(ratios[-1].quantize(Decimal("0.000001")), "f"),
            "queried_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "Azure Retail Prices API (list prices, UK South virtual machines)",
            "definition": "Median of (price in the target currency / price in the source currency) over items "
                          "listed in both currencies. This is today's list-price rate, not an invoice rate."}


# ---- storage -------------------------------------------------------------------------------------------

def _path(data_dir: Path) -> Path:
    return data_dir / "fx_rates.json"


def load(data_dir: Path) -> list[dict[str, Any]]:
    try:
        rows = json.loads(_path(data_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return sorted((r for r in rows if isinstance(r, dict)), key=lambda r: (r.get("month", ""), r.get("from", "")))


def decimal_rate(value: str) -> Decimal:
    try:
        rate = Decimal(value)
    except InvalidOperation as e:
        raise FxError("rate must be a number") from e
    if not rate.is_finite() or not 0 < rate < 1_000_000:
        raise FxError("rate must be greater than zero and below 1000000")
    with localcontext() as ctx:
        ctx.prec = 80
        if rate != rate.quantize(RATE_QUANTUM):
            raise FxError("rate must have at most 18 decimal places")
    return rate


def rates_to(target: str, rates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Express saved rates as from -> target, preferring a direct rate over its inverse."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rates:
        if r.get("from") == target and r.get("to") and r["to"] != target:
            with localcontext() as ctx:
                ctx.prec = 80
                inv = (Decimal(1) / decimal_rate(r["rate"])).quantize(RATE_QUANTUM, rounding=ROUND_HALF_EVEN)
            decimal_rate(str(inv))
            out.setdefault((r["to"], r["month"]), {"month": r["month"], "from": r["to"], "to": target,
                                                  "rate": format(inv, "f"), "inverted": True})
    for r in rates:
        if r.get("to") == target and r.get("from") != target:
            out[(r["from"], r["month"])] = r
    return list(out.values())


def _ix(month: str) -> int:
    year, number = month.split("-")
    return int(year) * 12 + int(number)


def resolve(target: str, rates: list[dict[str, Any]], needed: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Choose each own-month rate, otherwise the nearest saved month (earlier on a tie)."""
    direct: dict[str, dict[str, Decimal]] = {}
    for r in rates_to(target, rates):
        direct.setdefault(r["from"], {})[r["month"]] = decimal_rate(r["rate"])
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for ccy, month in needed:
        if ccy == target:
            continue
        have = direct.get(ccy)
        if not have:
            continue
        if month in have:
            out[(ccy, month)] = {"rate": have[month], "rate_month": month, "exact": True}
        else:
            near = min(have, key=lambda m: (abs(_ix(m) - _ix(month)), _ix(m)))
            out[(ccy, month)] = {"rate": have[near], "rate_month": near, "exact": False}
    return out


def save(data_dir: Path, month: str, from_ccy: str, to_ccy: str, rate: str, method: str, source: str,
         matched: int | None = None) -> dict[str, Any]:
    if not MONTH.match(month):
        raise FxError("month must look like 2026-09")
    if not CCY.match(from_ccy) or not CCY.match(to_ccy) or from_ccy == to_ccy:
        raise FxError("choose two different three-letter currencies")
    if method not in ("price-list", "manual"):
        raise FxError("method must be price-list or manual")
    r = decimal_rate(rate)
    row = {"month": month, "from": from_ccy, "to": to_ccy, "rate": format(r.normalize(), "f"), "method": method,
           "source": source[:200], "matched": matched, "saved_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with _lock:
        rows = [x for x in load(data_dir) if (x.get("month"), x.get("from"), x.get("to")) != (month, from_ccy, to_ccy)]
        rows.append(row)
        data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=data_dir, prefix="fx_rates.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, indent=1)
            os.replace(tmp, _path(data_dir))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return row
