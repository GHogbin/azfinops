"""Combined view: shows every currency in one target currency, using the rates saved on the Pricing screen.

Method: for the length of one request, the two aggregate tables are shadowed by TEMP views of the same name and
columns. Each row in the target currency is passed through untouched. Each row in another currency is multiplied by
the saved rate for its own month (money columns only; hours and counts are not money) and relabelled with the target
currency. Missing own-month rates use the nearest saved month, marked as carried. A currency without any saved
rate is left out, never converted at a guess; summary() reports exactly
which months those are. Temp objects belong to the request's own connection, so nothing is stored and no other
request sees them. The database itself is untouched.
"""
from __future__ import annotations

import re
import threading
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

from .fx import rates_to, resolve

__all__ = ["install", "rates_to", "register_decimal_conversion", "resolve", "summary"]

if TYPE_CHECKING:
    from .queries import Filters

_CCY = re.compile(r"^[A-Z]{3}$")
_function_lock = threading.Lock()


def _convert_money(amount: pa.ChunkedArray, rate: pa.ChunkedArray) -> pa.ChunkedArray:
    # The product needs up to 63 digits before its single half-even rescale.
    product = pc.multiply(pc.cast(amount, pa.decimal256(38, 18)), pc.cast(rate, pa.decimal256(24, 18)))
    rounded = pc.round(product, ndigits=18, round_mode="half_to_even")
    return pc.cast(rounded, pa.decimal128(38, 18))


def register_decimal_conversion(con: Any) -> None:
    with _function_lock:
        exists = con.execute(
            "SELECT count(*) FROM duckdb_functions() WHERE function_name = 'azfinops_fx_money'").fetchall()[0][0]
        if not exists:
            con.create_function("azfinops_fx_money", _convert_money,
                                [duckdb.decimal_type(38, 18), duckdb.decimal_type(24, 18)],
                                duckdb.decimal_type(38, 18), type="arrow")


def _prepare(con: Any, target: str, rates: list[dict[str, Any]],
             f: Filters | None = None) -> tuple[str, dict[tuple[str, str], dict[str, Any]]]:
    if not _CCY.match(target):
        raise ValueError("target currency must be a 3-letter ISO code")
    cat = str(con.execute("SELECT current_database()").fetchall()[0][0]).replace('"', '""')
    src = f'"{cat}".main'
    con.execute("CREATE OR REPLACE TEMP TABLE fx_rate "
                "(from_ccy VARCHAR, usage_month DATE, rate DECIMAL(24,18))")
    clauses: list[str] = []
    params: list[Any] = []
    if f is not None:
        clauses.append("cost_kind = ?")
        params.append(f.cost_kind)
        if f.billing_account_ids:
            clauses.append(f"billing_account_id IN ({','.join('?' * len(f.billing_account_ids))})")
            params.extend(f.billing_account_ids)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    # Comparisons and the all-dates summary may widen the dates after installation.
    # The scoped metadata supplies every required month without scanning the daily aggregate.
    source = "aggregate_scope_freshness" if f is not None else f"{src}.agg_daily"
    needed = [(str(c), d.isoformat()[:7]) for c, d in con.execute(
        f"SELECT DISTINCT billing_currency, usage_month FROM {source}{where}", params).fetchall()]
    use = resolve(target, rates, needed)
    rows = [(c, f"{m}-01", v["rate"]) for (c, m), v in use.items()]
    if rows:
        con.executemany("INSERT INTO fx_rate VALUES (?, CAST(? AS DATE), ?)", rows)
    register_decimal_conversion(con)
    # Cache only selected foreign money columns. Widened comparisons still convert cache misses exactly.
    con.execute("CREATE OR REPLACE TEMP TABLE fx_daily_money "
                "(row_id BIGINT, cost DECIMAL(38,18), payg_equivalent DECIMAL(38,18), "
                "cost_with_payg DECIMAL(38,18))")
    if f is not None and (f.start or f.end):
        bounds = [*clauses, "d.billing_currency <> ?"]
        values = [*params, target]
        if f.start:
            bounds.append("d.usage_date >= ?")
            values.append(f.start)
        if f.end:
            bounds.append("d.usage_date <= ?")
            values.append(f.end)
        con.execute(
            "INSERT INTO fx_daily_money SELECT d.rowid, azfinops_fx_money(d.cost, x.rate), "
            "azfinops_fx_money(d.payg_equivalent, x.rate), azfinops_fx_money(d.cost_with_payg, x.rate) "
            f"FROM {src}.agg_daily d JOIN fx_rate x "
            "ON x.from_ccy = d.billing_currency AND x.usage_month = d.usage_month "
            f"WHERE {' AND '.join(bounds)}", values)
    return src, use


def install(con: Any, target: str, rates: list[dict[str, Any]], f: Filters | None = None) -> None:
    src, _ = _prepare(con, target, rates, f)

    def conv(col: str, cached: bool = False) -> str:
        cache = f"WHEN c.row_id IS NOT NULL THEN c.{col} " if cached else ""
        return (f"CASE WHEN d.billing_currency = '{target}' THEN d.{col} "
                f"{cache}ELSE azfinops_fx_money(d.{col}, x.rate) END AS {col}")

    join = (f"FROM {src}.%s d LEFT JOIN fx_rate x ON x.from_ccy = d.billing_currency AND x.usage_month = d.usage_month "
            f"WHERE d.billing_currency = '{target}' OR x.rate IS NOT NULL")
    con.execute(f"CREATE OR REPLACE TEMP VIEW agg_daily AS SELECT d.* REPLACE ('{target}' AS billing_currency, "
                f"{conv('cost', True)}, {conv('payg_equivalent', True)}, {conv('cost_with_payg', True)}) "
                f"FROM {src}.agg_daily d LEFT JOIN fx_rate x "
                "ON x.from_ccy = d.billing_currency AND x.usage_month = d.usage_month "
                "LEFT JOIN fx_daily_money c ON c.row_id = d.rowid "
                f"WHERE d.billing_currency = '{target}' OR x.rate IS NOT NULL")
    con.execute(f"CREATE OR REPLACE TEMP VIEW agg_resource_month AS SELECT d.* REPLACE ('{target}' AS billing_currency, "
                f"{conv('cost')}) " + join % "agg_resource_month")


def summary(con: Any, cost_kind: str, target: str, rates: list[dict[str, Any]],
            f: Filters | None = None) -> dict[str, Any]:
    """Per month and currency: native cost, the rate used and where it came from, and the converted cost. Read from the
    real table. status: target (not converted), converted (the month's own rate), carried (another month's rate,
    estimated), missing (no rate saved for that currency at all, so left out)."""
    src, use = _prepare(con, target, rates, f)
    data = con.execute(
        f"SELECT d.usage_month, d.billing_currency, sum(d.cost), "
        f"sum(CASE WHEN d.billing_currency = '{target}' THEN d.cost ELSE azfinops_fx_money(d.cost, x.rate) END) "
        f"FROM {src}.agg_daily d LEFT JOIN fx_rate x "
        "ON x.from_ccy = d.billing_currency AND x.usage_month = d.usage_month "
        "WHERE d.cost_kind = ? GROUP BY 1, 2 ORDER BY 1, 2", [cost_kind]).fetchall()
    out = []
    for month, ccy, cost, converted in data:
        m = month.isoformat()[:7]
        c = Decimal(cost)
        if ccy == target:
            out.append({"month": m, "currency": ccy, "native": c, "rate": None, "rate_month": None, "converted": c,
                        "status": "target"})
            continue
        u = use.get((ccy, m))
        if u is None:
            out.append({"month": m, "currency": ccy, "native": c, "rate": None, "rate_month": None, "converted": None,
                        "status": "missing"})
        else:
            out.append({"month": m, "currency": ccy, "native": c, "rate": u["rate"], "rate_month": u["rate_month"],
                        "converted": converted, "status": "converted" if u["exact"] else "carried"})
    return {"target": target, "rows": out,
            "missing": sorted({(x["month"], x["currency"]) for x in out if x["status"] == "missing"}),
            "carried": sorted({(x["month"], x["currency"], x["rate_month"]) for x in out if x["status"] == "carried"}),
            "definition": "Each month's cost in another currency is multiplied by the rate saved for that month and "
                          "added to the target-currency cost. A month with no saved rate uses the nearest month's "
                          "saved rate for the same currency (marked as carried). A currency with no saved rate at all "
                          "is left out. Rates are list-price or hand-entered rates, so combined figures will not "
                          "reconcile to any invoice. Rates support 18 decimal places; inverse rates and each converted "
                          "aggregate amount are rounded to 18 decimal places, ties to even, without floating point."}
