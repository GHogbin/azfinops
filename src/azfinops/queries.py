"""Read-only analytical queries over the aggregates (never the raw facts).

Every query requires ONE cost basis and ONE native currency, so actual vs amortized and different
currencies can never be summed. Rankings are computed over all groups before any LIMIT, and an
"other" remainder is returned so a truncated list still reconciles to its total.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

from .canonical import COST_KINDS
from .finance.calc import price_volume, sum_decimal

_CCY = re.compile(r"^[A-Z]{3}$")
ZERO = Decimal(0)
# filter attribute -> agg_daily column
_FIELDS = {
    "billing_account_ids": "billing_account_id", "subscription_ids": "subscription_id",
    "applications": "application", "environments": "environment", "services": "meter_category",
    "regions": "region", "resource_groups": "resource_group", "charge_types": "charge_type",
    "pricing_models": "pricing_model", "publisher_types": "publisher_type",
    "meter_subcategories": "meter_subcategory",
}
DIMENSIONS = {"billing_account": "billing_account_id", "meter_subcategory": "meter_subcategory",
              "subscription": "subscription_id", "service": "meter_category", "resource_group": "resource_group",
              "application": "application", "environment": "environment", "region": "region",
              "pricing_model": "pricing_model", "publisher_type": "publisher_type", "charge_type": "charge_type"}


@dataclass(frozen=True)
class Filters:
    cost_kind: str
    currency: str
    start: date | None = None
    end: date | None = None
    billing_account_ids: tuple[str, ...] = ()
    subscription_ids: tuple[str, ...] = ()
    applications: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    resource_groups: tuple[str, ...] = ()
    charge_types: tuple[str, ...] = ()
    pricing_models: tuple[str, ...] = ()
    publisher_types: tuple[str, ...] = ()
    meter_subcategories: tuple[str, ...] = ()
    combined: bool = False      # show every currency converted into `currency` (see combined.py)

    def __post_init__(self) -> None:
        if self.cost_kind not in COST_KINDS:
            raise ValueError(f"cost_kind must be one of {sorted(COST_KINDS)}")
        if not _CCY.match(self.currency):
            raise ValueError("currency must be a 3-letter ISO code")
        if self.start and self.end and self.start > self.end:
            raise ValueError("start is after end")

    def where(self, exclude: tuple[str, ...] = ()) -> tuple[str, list[Any]]:
        """Parameterised WHERE over agg_daily. `exclude` names filter attributes to leave out
        (a picker lists the values of a dimension while ignoring that dimension's own selection)."""
        clauses = ["cost_kind = ?", "billing_currency = ?"]
        params: list[Any] = [self.cost_kind, self.currency]
        if self.start:
            clauses.append("usage_date >= ?"); params.append(self.start)
        if self.end:
            clauses.append("usage_date <= ?"); params.append(self.end)
        for attr, col in _FIELDS.items():
            if attr in exclude:
                continue
            vals = getattr(self, attr)
            if vals:
                clauses.append(f"{col} IN ({','.join('?' * len(vals))})"); params.extend(vals)
        return " AND ".join(clauses), params


def _d(x: Any) -> Decimal:
    return ZERO if x is None else Decimal(x)


def totals(con: DuckDBPyConnection, f: Filters) -> dict[str, Any]:
    w, p = f.where()
    rows = con.execute(f"SELECT charge_type, sum(cost), sum(row_count) FROM agg_daily WHERE {w} "
                       "GROUP BY 1 ORDER BY 1", p).fetchall()
    by = {r[0]: _d(r[1]) for r in rows}
    return {"total": sum_decimal(by.values()), "by_charge_type": by,
            "usage": by.get("Usage", ZERO), "rows": sum(int(r[2]) for r in rows)}


def series(con: DuckDBPyConnection, f: Filters, grain: str = "day") -> list[dict[str, Any]]:
    col = {"day": "usage_date", "month": "usage_month"}[grain]
    w, p = f.where()
    rows = con.execute(f"SELECT {col}, sum(cost), sum(cost) FILTER (WHERE charge_type = 'Usage') "
                       f"FROM agg_daily WHERE {w} GROUP BY 1 ORDER BY 1", p).fetchall()
    complete = {r["month"]: r["complete"] for r in period_freshness(con, f)} if grain == "month" else {}
    return [{"period": r[0], "total": _d(r[1]), "usage": _d(r[2]), "complete": complete.get(r[0])} for r in rows]


def series_by_account(con: DuckDBPyConnection, f: Filters, grain: str = "day") -> list[dict[str, Any]]:
    """The same series as `series`, one row per enrollment per period, so a chart can stack the enrollments.
    Every row is in the filter currency (converted in the Combined view), so the rows of a period add up to its total."""
    col = {"day": "usage_date", "month": "usage_month"}[grain]
    w, p = f.where()
    rows = con.execute(f"SELECT {col}, billing_account_id, sum(cost), sum(cost) FILTER (WHERE charge_type = 'Usage') "
                       f"FROM agg_daily WHERE {w} GROUP BY 1, 2 ORDER BY 1, 2", p).fetchall()
    return [{"period": r[0], "account": r[1], "total": _d(r[2]), "usage": _d(r[3])} for r in rows]


def top(con: DuckDBPyConnection, f: Filters, dimension: str, n: int = 10) -> dict[str, Any]:
    col = DIMENSIONS[dimension]
    n = max(1, min(int(n), 100))
    w, p = f.where()
    rows = con.execute(f"SELECT {col}, sum(cost) AS c FROM agg_daily WHERE {w} GROUP BY 1 "
                       f"ORDER BY c DESC, 1 LIMIT {n}", p).fetchall()
    all_total = _d(con.execute(f"SELECT sum(cost) FROM agg_daily WHERE {w}", p).fetchall()[0][0])
    groups = _d(con.execute(f"SELECT count(*) FROM (SELECT 1 FROM agg_daily WHERE {w} GROUP BY {col})", p)
                .fetchall()[0][0])
    shown = [{"key": r[0], "cost": _d(r[1]), "split": []} for r in rows]
    # Per-enrollment split of each row and of the remainder, so every row can show where its cost comes from.
    per_acct = {a: _d(c) for a, c in con.execute(
        f"SELECT billing_account_id, sum(cost) FROM agg_daily WHERE {w} GROUP BY 1 ORDER BY 1", p).fetchall()}
    other_split = dict(per_acct)
    if shown and len(per_acct) > 1:
        keys = [r["key"] for r in shown if r["key"] is not None]
        cond = f"{col} IN ({','.join('?' * len(keys))})" if keys else "1 = 0"
        if any(r["key"] is None for r in shown):
            cond = f"({cond} OR {col} IS NULL)"
        cell: dict[tuple[Any, str], Decimal] = {}
        for k, a, c in con.execute(f"SELECT {col}, billing_account_id, sum(cost) FROM agg_daily WHERE {w} AND {cond} "
                                   "GROUP BY 1, 2", [*p, *keys]).fetchall():
            cell[(k, a)] = _d(c)
        for r in shown:
            r["split"] = [{"account": a, "cost": cell.get((r["key"], a), ZERO)} for a in per_acct]
            for a in per_acct:
                other_split[a] -= cell.get((r["key"], a), ZERO)
    return {"dimension": dimension, "rows": shown, "total": all_total, "groups": int(groups),
            "other": all_total - sum((r["cost"] for r in shown), ZERO),
            "other_split": [{"account": a, "cost": c} for a, c in other_split.items()] if len(per_acct) > 1 else []}


def commitments(con: DuckDBPyConnection, f: Filters) -> dict[str, Any]:
    """Utilization, coverage and observed discount. Amortized basis only (needs Unused* rows)."""
    if f.cost_kind != "amortized":
        return {"available": False, "reason": "Needs the amortized export (it carries unused commitment rows)."}
    w, p = f.where()
    used, unused = con.execute(
        f"SELECT sum(quantity_hours) FILTER (WHERE charge_type = 'Usage' AND pricing_model = 'Reservation'), "
        f"sum(quantity_hours) FILTER (WHERE charge_type = 'UnusedReservation') FROM agg_daily WHERE {w}", p).fetchall()[0]
    used, unused = _d(used), _d(unused)
    util = None if used + unused == 0 else used / (used + unused)
    used_cost, unused_res_cost = con.execute(
        f"SELECT sum(cost) FILTER (WHERE charge_type = 'Usage' AND pricing_model = 'Reservation'), "
        f"sum(cost) FILTER (WHERE charge_type = 'UnusedReservation') FROM agg_daily WHERE {w}", p).fetchall()[0]
    used_cost, unused_res_cost = _d(used_cost), _d(unused_res_cost)
    cost_total = used_cost + unused_res_cost
    util_cost = None if cost_total == 0 else used_cost / cost_total
    covered, uncovered, missing, contract, gross_cost = con.execute(f"""
        WITH s AS (SELECT * FROM agg_daily WHERE {w}),
        cats AS (SELECT DISTINCT meter_category FROM s
                 WHERE charge_type = 'Usage' AND pricing_model IN ('Reservation','SavingsPlan'))
        SELECT sum(payg_equivalent) FILTER (WHERE pricing_model IN ('Reservation','SavingsPlan')),
               sum(payg_equivalent) FILTER (WHERE pricing_model = 'On Demand'),
               sum(payg_missing_rows), sum(contract_basis_rows),
               sum(cost_with_payg) FILTER (WHERE pricing_model IN ('Reservation','SavingsPlan'))
        FROM s WHERE charge_type = 'Usage' AND COALESCE(publisher_type, '') <> 'Marketplace'
          AND meter_category IN (SELECT meter_category FROM cats)""", p).fetchall()[0]
    covered, uncovered = _d(covered), _d(uncovered)
    eligible = covered + uncovered
    unused_cost = _d(con.execute(
        f"SELECT sum(cost) FILTER (WHERE charge_type IN ('UnusedReservation','UnusedSavingsPlan')) "
        f"FROM agg_daily WHERE {w}", p).fetchall()[0][0])
    gross = covered - _d(gross_cost)
    return {"available": True,
            "utilization": {"used_hours": used, "unused_hours": unused, "ratio": util,
                            "used_cost": used_cost, "unused_cost": unused_res_cost, "ratio_cost": util_cost,
                            "definition": "ratio: used / (used + unused) hours, reservations only. Hours of "
                                          "different reservations are different units (vCore-hours vs "
                                          "instance-hours), so this blend is dominated by large reservations. "
                                          "ratio_cost: used / (used + unused) amortized cost, i.e. the share of "
                                          "reservation spend that was used; per reservation it equals the hours "
                                          "ratio. Savings plans have no hour denominator in this data"},
            "coverage": {"covered": covered, "eligible": eligible,
                         "ratio": None if eligible == 0 else covered / eligible,
                         "excluded_missing_price_rows": int(missing or 0),
                         "contract_basis_rows": int(contract or 0),
                         "definition": "Baseline-price value of benefit-covered usage / (covered + on-demand usage) "
                                       "within meter categories that have any covered usage; Spot and "
                                       "Marketplace excluded. The baseline is the PayG price when the export "
                                       "carries one, otherwise the contract unit price. Eligibility is "
                                       "inferred from the data, not a published list."},
            "observed_discount": {"payg_equivalent": covered, "benefit_cost": _d(gross_cost), "gross": gross,
                                  "unused_cost": unused_cost, "net": gross - unused_cost,
                                  "definition": "Historical, not an estimate: the baseline-price value of covered usage minus "
                                                "what it cost. The baseline is the contract unit price when the "
                                                "export has no PayG price (the default); see contract_basis_rows."}}


MAX_PAGE_SIZE = 200
_ATTR_FOR_DIMENSION = {"subscription": "subscription_ids", "service": "services", "resource_group": "resource_groups",
                       "application": "applications", "environment": "environments", "region": "regions",
                       "pricing_model": "pricing_models", "publisher_type": "publisher_types",
                       "charge_type": "charge_types", "billing_account": "billing_account_ids",
                       "meter_subcategory": "meter_subcategories"}


def explore(con: DuckDBPyConnection, f: Filters, group_by: list[str], sort: str = "cost", descending: bool = True,
            page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """Server-side paged group-by over 1-2 whitelisted dimensions. Every figure reconciles to the filter
    total: `total` is over ALL groups, not just the page. Order is deterministic (ties broken by key)."""
    if not 1 <= len(group_by) <= 2 or len(set(group_by)) != len(group_by):
        raise ValueError("group_by needs one or two distinct dimensions")
    bad = [g for g in group_by if g not in DIMENSIONS]
    if bad:
        raise ValueError(f"unknown dimension(s): {', '.join(bad)}")
    if sort not in ("cost", "key"):
        raise ValueError("sort must be 'cost' or 'key'")
    page, page_size = int(page), max(1, min(int(page_size), MAX_PAGE_SIZE))
    if page < 1 or (page - 1) * page_size > 1_000_000:
        raise ValueError("page out of range")
    cols = [DIMENSIONS[g] for g in group_by]
    sel = ", ".join(cols)
    direction = "DESC" if descending else "ASC"
    order = f"c {direction}, {sel}" if sort == "cost" else ", ".join(f"{c} {direction}" for c in cols)
    w, p = f.where()
    rows = con.execute(
        f"SELECT {sel}, sum(cost) AS c, sum(row_count) AS n FROM agg_daily WHERE {w} GROUP BY {sel} "
        f"ORDER BY {order} LIMIT {page_size} OFFSET {(page - 1) * page_size}", p).fetchall()
    groups, total = con.execute(
        f"SELECT count(*), sum(c) FROM (SELECT sum(cost) AS c FROM agg_daily WHERE {w} GROUP BY {sel})", p
    ).fetchall()[0]
    k = len(cols)
    return {"group_by": group_by, "page": page, "page_size": page_size, "groups": int(groups),
            "pages": max(1, -(-int(groups) // page_size)), "total": _d(total),
            "rows": [{"keys": [r[i] for i in range(k)], "cost": _d(r[k]), "rows": int(r[k + 1])} for r in rows]}


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def dimension_values(con: DuckDBPyConnection, f: Filters, dimension: str, search: str = "", limit: int = 50) -> dict[str, Any]:
    """Values of one dimension within the current filters (ignoring that dimension's own selection),
    biggest cost first, for filter pickers. Subscriptions are searchable by id or name."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension: {dimension}")
    col, limit = DIMENSIONS[dimension], max(1, min(int(limit), 200))
    w, p = f.where(exclude=(_ATTR_FOR_DIMENSION[dimension],))
    extra, ep = "", []
    if search.strip():
        pat = _like(search.strip()[:100])
        if dimension == "subscription":
            extra = (f" AND ({col} ILIKE ? ESCAPE '\\' OR lower({col}) IN "
                     "(SELECT subscription_id FROM dim_subscription WHERE subscription_name ILIKE ? ESCAPE '\\'))")
            ep = [pat, pat]
        else:
            extra, ep = f" AND {col} ILIKE ? ESCAPE '\\'", [pat]
    rows = con.execute(f"SELECT {col}, sum(cost) AS c FROM agg_daily WHERE {w}{extra} GROUP BY {col} "
                       f"ORDER BY c DESC, {col} LIMIT {limit}", p + ep).fetchall()
    return {"dimension": dimension, "values": [{"value": r[0], "cost": _d(r[1])} for r in rows],
            "labels": subscription_labels(con, [r[0] for r in rows]) if dimension == "subscription" else {}}


def subscription_labels(con: DuckDBPyConnection, ids: list[Any]) -> dict[str, str]:
    """{subscription id as stored in facts: display name}. Joins on lower-cased id; empty if no names."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    names = {r[0]: r[1] for r in con.execute(
        f"SELECT subscription_id, subscription_name FROM dim_subscription WHERE subscription_id IN ({ph})",
        [i.lower() for i in ids]).fetchall()}
    return {i: names[i.lower()] for i in ids if i.lower() in names}


def _resource_where(f: Filters, dates: bool = True) -> tuple[str, list[Any], list[str]]:
    """WHERE for agg_resource_month (monthly grain). Returns (clause, params, filters that cannot apply here).

    Dates widen to whole months. Region, publisher type and charge type are not columns of this table; they are reported back so a screen can say so instead of silently ignoring them."""
    clauses = ["cost_kind = ?", "billing_currency = ?"]
    params: list[Any] = [f.cost_kind, f.currency]
    if dates and f.start:
        clauses.append("usage_month >= CAST(date_trunc('month', CAST(? AS DATE)) AS DATE)"); params.append(f.start)
    if dates and f.end:
        clauses.append("usage_month <= CAST(date_trunc('month', CAST(? AS DATE)) AS DATE)"); params.append(f.end)
    for attr, col in (("subscription_ids", "subscription_id"), ("applications", "application"),
                      ("environments", "environment"), ("services", "meter_category"),
                      ("resource_groups", "resource_group"), ("pricing_models", "pricing_model"),
                      ("meter_subcategories", "meter_subcategory"), ("billing_account_ids", "billing_account_id")):
        vals = getattr(f, attr)
        if vals:
            clauses.append(f"{col} IN ({','.join('?' * len(vals))})"); params.extend(vals)
    ignored = [a for a in ("regions", "charge_types", "publisher_types") if getattr(f, a)]
    return " AND ".join(clauses), params, ignored


def service_breakdown(con: DuckDBPyConnection, f: Filters, service: str, n: int = 25) -> dict[str, Any]:
    """The meters inside one service (meter category), largest first: one row per meter name and unit.

    Azure gives the same meter (for example "D4s v5") a separate meter ID per region and per variant (Linux,
    Windows, low priority), so rows are merged by name and unit and the IDs behind each are listed as its parts,
    with their sub-category and region. Reads agg_resource_month (monthly grain), so dates widen to whole months
    and region, charge type and publisher filters cannot apply here; they are reported back. All charge types are
    included, so the total equals the service's total on the Explorer whenever those filters are not in use.
    Quantities are for usage rows only and comparable within one row, because its parts share a name and unit."""
    n = max(1, min(int(n), 200))
    w, params, ignored = _resource_where(replace(f, services=(service,)))
    rows = con.execute(f"""
        SELECT meter_name, unit, meter_id, max(meter_subcategory), max(meter_region), sum(cost),
               sum(cost) FILTER (WHERE pricing_model = 'On Demand'),
               sum(quantity) FILTER (WHERE charge_type = 'Usage')
        FROM agg_resource_month WHERE {w} GROUP BY meter_name, unit, meter_id""", params).fetchall()
    if not rows:
        return {"available": False, "service": service,
                "reason": "No cost for this service with the current filters at monthly grain."}
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for name, unit, mid, sub, region, c, od, qty in rows:
        key = (name or f"(no name) {mid or ''}".strip(), unit or "")
        m = merged.setdefault(key, {"name": name, "unit": unit or "", "meter_id": mid or "", "cost": ZERO,
                                    "on_demand_cost": ZERO, "quantity": None, "parts": []})
        m["cost"] += _d(c)
        m["on_demand_cost"] += _d(od)
        if qty is not None:
            m["quantity"] = (m["quantity"] or ZERO) + _d(qty)
        m["parts"].append({"meter_id": mid or "", "subcategory": sub, "region": region, "cost": _d(c),
                           "quantity": None if qty is None else _d(qty)})
    items = list(merged.values())
    for m in items:
        m["parts"].sort(key=lambda x: (-x["cost"], x["meter_id"]))
        m["variants"] = len(m["parts"])
        m["meter_id"] = m["parts"][0]["meter_id"]            # the largest part, kept for links and CSV
        m["parts"] = m["parts"][:20]
    items.sort(key=lambda r: (-r["cost"], str(r["name"]), r["unit"]))
    total = sum((r["cost"] for r in items), ZERO)
    shown = items[:n]
    for r in shown:
        r["share"] = None if total == 0 else r["cost"] / total
    return {"available": True, "service": service, "total": total, "groups": len(items), "rows": shown,
            "other": total - sum((r["cost"] for r in shown), ZERO), "filters_not_applied": ignored,
            "grain": "month",
            "definition": ("Cost per meter inside this service, all charge types, at monthly grain: one row per meter name "
                           "and unit, merging the separate meter IDs Azure uses for each region and variant (the row "
                           "lists them). Quantity counts usage rows only. Dates widen to whole months.")}


def reservations(con: DuckDBPyConnection, f: Filters, n: int = 15) -> dict[str, Any]:
    """Utilization per reservation (hours are comparable within one reservation), worst waste first.

    Reads agg_resource_month, which is monthly: start/end are widened to whole months. Filters on
    region, charge type, pricing model and publisher type are not available at this grain and are
    reported in `filters_not_applied` instead of being silently dropped.
    """
    if f.cost_kind != "amortized":
        return {"available": False, "reason": "Needs the amortized export (it carries unused commitment rows)."}
    w, params, ignored = _resource_where(f)
    hour = "lower(trim(unit)) = '1 hour'"
    rows = con.execute(f"""
        SELECT COALESCE(commitment_id, '(unknown)') AS cid, max(reservation_name) AS name,
               max(meter_category) AS cat,
               sum(quantity) FILTER (WHERE charge_type = 'Usage' AND pricing_model = 'Reservation' AND {hour}),
               sum(quantity) FILTER (WHERE charge_type = 'UnusedReservation' AND {hour}),
               sum(cost) FILTER (WHERE charge_type = 'Usage' AND pricing_model = 'Reservation'),
               sum(cost) FILTER (WHERE charge_type = 'UnusedReservation')
        FROM agg_resource_month
        WHERE {w} AND (charge_type = 'UnusedReservation' OR (charge_type = 'Usage' AND pricing_model = 'Reservation'))
        GROUP BY 1""", params).fetchall()
    out = []
    for cid, name, cat, uh, nh, uc, nc in rows:
        uh, nh, uc, nc = _d(uh), _d(nh), _d(uc), _d(nc)
        out.append({"commitment_id": cid, "name": name, "meter_category": cat, "used_hours": uh,
                    "unused_hours": nh, "used_cost": uc, "unused_cost": nc,
                    "utilization": None if uh + nh == 0 else uh / (uh + nh)})
    out.sort(key=lambda r: (-r["unused_cost"], r["commitment_id"]))
    return {"available": True, "count": len(out), "rows": out[:max(1, min(int(n), 200))],
            "without_used_rows": sum(1 for r in out if r["used_hours"] == 0 and r["unused_hours"] > 0),
            "without_unused_rows_count": sum(1 for r in out if r["unused_cost"] == 0),
            "total_unused_cost": sum((r["unused_cost"] for r in out), ZERO),
            "filters_not_applied": ignored,
            "grain": "month"}


_COV_BASE = "charge_type = 'Usage' AND COALESCE(publisher_type, '') <> 'Marketplace'"


def _commitment_exprs(elig: str = "") -> dict[str, str]:
    """Aggregate expressions shared by the monthly and per-category views. `elig` restricts the coverage
    measures to categories inferred as eligible, exactly as commitments() does for the headline."""
    benefit = "pricing_model IN ('Reservation','SavingsPlan')"
    return {
        "used_cost": "sum(cost) FILTER (WHERE charge_type = 'Usage' AND pricing_model = 'Reservation')",
        "savings_plan_cost": "sum(cost) FILTER (WHERE charge_type = 'Usage' AND pricing_model = 'SavingsPlan')",
        "unused_reservation": "sum(cost) FILTER (WHERE charge_type = 'UnusedReservation')",
        "unused_savings_plan": "sum(cost) FILTER (WHERE charge_type = 'UnusedSavingsPlan')",
        "covered": f"sum(payg_equivalent) FILTER (WHERE {_COV_BASE} AND {benefit}{elig})",
        "on_demand": f"sum(payg_equivalent) FILTER (WHERE {_COV_BASE} AND pricing_model = 'On Demand'{elig})",
        "benefit_cost": f"sum(cost_with_payg) FILTER (WHERE {_COV_BASE} AND {benefit}{elig})",
        # presence of any benefit usage row decides eligibility, exactly like the headline's `cats`
        "benefit_rows": "sum(row_count) FILTER (WHERE charge_type = 'Usage' AND pricing_model IN ('Reservation','SavingsPlan'))",
    }


def _commitment_row(r: dict[str, Any]) -> dict[str, Any]:
    used, unused_res = _d(r["used_cost"]), _d(r["unused_reservation"])
    covered, on_demand, benefit = _d(r["covered"]), _d(r["on_demand"]), _d(r["benefit_cost"])
    unused_total = unused_res + _d(r["unused_savings_plan"])
    gross = covered - benefit
    return {"used_cost": used, "savings_plan_cost": _d(r["savings_plan_cost"]), "unused_reservation": unused_res,
            "unused_savings_plan": _d(r["unused_savings_plan"]), "unused_total": unused_total,
            "utilization": None if used + unused_res == 0 else used / (used + unused_res),
            "covered": covered, "on_demand": on_demand,
            "coverage": None if covered + on_demand == 0 else covered / (covered + on_demand),
            "benefit_cost": benefit, "gross": gross, "net": gross - unused_total}


def commitments_detail(con: DuckDBPyConnection, f: Filters, limit: int = 100) -> dict[str, Any]:
    """Headline commitment metrics plus the month and meter-category breakdowns behind them.

    Every breakdown uses the headline's definitions, so months sum to the headline coverage inputs and
    categories sum to them as well. Only categories with commitment evidence are listed: those with benefit
    usage (eligible=True) and any other category that carries unused commitment cost (eligible=False, outside
    the coverage denominator). Amortized basis only."""
    head = commitments(con, f)
    if not head["available"]:
        return head
    w, p = f.where()
    cats = ("WITH base AS (SELECT * FROM agg_daily WHERE " + w + "), cats AS (SELECT DISTINCT meter_category "
            "FROM base WHERE charge_type = 'Usage' AND pricing_model IN ('Reservation','SavingsPlan')), "
            "s AS (SELECT base.*, base.meter_category IN (SELECT meter_category FROM cats) AS elig FROM base) ")
    elig = " AND elig"
    ex = _commitment_exprs(elig)
    names = list(ex)
    months = con.execute(cats + f"SELECT usage_month, {', '.join(ex.values())} FROM s GROUP BY 1 ORDER BY 1", p).fetchall()
    by_month = [{"month": m[0], **_commitment_row(dict(zip(names, m[1:], strict=True)))} for m in months]
    # per category: coverage measures are unrestricted here; eligibility is reported per row instead
    ex_cat = _commitment_exprs()
    names_c = list(ex_cat)
    rows = con.execute(
        f"SELECT meter_category, {', '.join(ex_cat.values())} FROM agg_daily WHERE {w} GROUP BY 1", p).fetchall()
    cat_rows = []
    for m in rows:
        raw = dict(zip(names_c, m[1:], strict=True))
        row = _commitment_row(raw)
        eligible = _d(raw["benefit_rows"]) > 0
        if not eligible and row["unused_total"] == 0:
            continue                    # no commitment evidence in this category: not shown on this screen
        if not eligible:
            row["coverage"] = None      # outside the coverage denominator
        cat_rows.append({"category": m[0], "eligible": eligible, **row})
    cat_rows.sort(key=lambda r: (-r["unused_total"], -r["on_demand"], str(r["category"])))
    spend = {k: sum((r[k] for r in by_month), ZERO)
             for k in ("used_cost", "savings_plan_cost", "unused_reservation", "unused_savings_plan")}
    return {**head, "spend": spend, "by_month": by_month, "by_category": cat_rows[:max(1, min(int(limit), 200))],
            "category_count": len(cat_rows)}


def movement(con: DuckDBPyConnection, f: Filters, month_from: date, month_to: date, n: int = 15) -> dict[str, Any]:
    """Why did usage cost change between two months? Per (meter, unit): volume = (q1 - q0) x p0 and
    price = c1 - q1 x p0, plus new and removed meters and groups with no unit price (see finance.calc).
    Reads agg_resource_month (monthly grain). The pieces always add up to the total change."""
    for m in (month_from, month_to):
        if m.day != 1:
            raise ValueError("months must be given as the first day of the month, e.g. 2026-09-01")
    if month_from == month_to:
        raise ValueError("choose two different months")
    n = max(1, min(int(n), 100))
    w, params, ignored = _resource_where(f, dates=False)

    def load(m: date) -> dict[tuple[str, str], tuple[Decimal, Decimal, str | None, str | None]]:
        out = {}
        for mid, unit, name, cat, qty, cost in con.execute(
                f"SELECT meter_id, unit, max(meter_name), max(meter_category), sum(quantity), sum(cost) "
                f"FROM agg_resource_month WHERE {w} AND usage_month = ? AND charge_type = 'Usage' "
                "GROUP BY meter_id, unit", params + [m]).fetchall():
            out[(mid or "", unit or "")] = (_d(qty), _d(cost), name, cat)
        return out

    g0, g1 = load(month_from), load(month_to)
    missing = [m.isoformat()[:7] for m, g in ((month_from, g0), (month_to, g1)) if not g]
    if missing:
        return {"available": False, "reason": f"No usage in {', '.join(missing)} for the current filters."}
    complete = {r["month"]: r["complete"] for r in period_freshness(
        con, replace(f, start=min(month_from, month_to), end=_month_end(max(month_from, month_to))))}
    if not all(complete.get(m, False) for m in (month_from, month_to)):
        return {"available": False, "reason": "Price and volume comparisons need two complete months for "
                "every selected enrollment and currency.", "filters_not_applied": ignored}

    def rows(g: dict[tuple[str, str], tuple[Decimal, Decimal, str | None, str | None]]) -> list[dict[str, Any]]:
        return [{"cost_kind": f.cost_kind, "billing_currency": f.currency, "charge_type": "Usage",
                 "meter_id": k[0], "unit": k[1], "quantity": v[0], "cost": v[1]} for k, v in g.items()]

    pv = price_volume(rows(g0), rows(g1), f.currency)
    movers: list[dict[str, Any]] = []
    for k in set(g0) | set(g1):
        a, b = g0.get(k), g1.get(k)
        name, cat = (b or a)[2:4]  # type: ignore[index]
        base = {"meter_id": k[0], "unit": k[1], "name": name, "category": cat,
                "quantity_from": a[0] if a else None, "quantity_to": b[0] if b else None,
                "cost_from": a[1] if a else ZERO, "cost_to": b[1] if b else ZERO}
        if a is None:
            row = {**base, "kind": "new", "change": b[1], "volume": None, "price": None}      # type: ignore[index]
        elif b is None:
            row = {**base, "kind": "removed", "change": -a[1], "volume": None, "price": None}
        elif a[0] == 0 or b[0] == 0:
            row = {**base, "kind": "no_unit_price", "change": b[1] - a[1], "volume": None, "price": None}
        else:
            v, p = pv.detail[k]
            row = {**base, "kind": "price_volume", "change": b[1] - a[1], "volume": v, "price": p}
        movers.append(row)
    movers.sort(key=lambda r: (-abs(r["change"]), str(r["name"]), r["meter_id"], r["unit"]))
    shown = movers[:n]
    parts = pv.volume + pv.price + pv.new + pv.removed + pv.unattributed
    return {"available": True, "month_from": month_from, "month_to": month_to, "change": pv.change,
            "volume": pv.volume, "price": pv.price, "new": pv.new, "removed": pv.removed,
            "no_unit_price": pv.unattributed, "reconciles": abs(parts - pv.change) < Decimal("0.000001"),
            "groups": len(movers), "rows": shown, "other": pv.change - sum((r["change"] for r in shown), ZERO),
            "filters_not_applied": ignored,
            "definition": "Per meter and unit: volume = (quantity change) x (previous unit price); price = cost "
                          "change minus volume. Meters present in only one month are new or removed; meters "
                          "with zero quantity in either month have no unit price and are not split."}


def applications(con: DuckDBPyConnection, f: Filters, n: int = 10) -> dict[str, Any]:
    """How much cost can be tied to an application, and where the gaps are.

    attributed = a configured application tag key had a value; unattributed = tagged, but none of the configured
    keys; untagged = no tags at all. The three always add up to the filtered total.
    """
    from .aggregates import NO_ENVIRONMENT, UNATTRIBUTED, UNTAGGED
    n = max(1, min(int(n), 100))
    w, p = f.where()
    case = (f"CASE application WHEN '{UNATTRIBUTED}' THEN 'unattributed' WHEN '{UNTAGGED}' THEN 'untagged' "
            f"ELSE 'attributed' END")
    st = {r[0]: (_d(r[1]), int(r[2])) for r in con.execute(
        f"SELECT {case}, sum(cost), count(DISTINCT application) FROM agg_daily WHERE {w} GROUP BY 1", p).fetchall()}
    z = (ZERO, 0)
    total = sum((v[0] for v in st.values()), ZERO)
    env_none = _d(con.execute(f"SELECT sum(cost) FROM agg_daily WHERE {w} AND environment = ?",
                              [*p, NO_ENVIRONMENT]).fetchall()[0][0])
    gap_where = f"{w} AND application IN (?, ?)"
    gp = [*p, UNATTRIBUTED, UNTAGGED]
    rows = con.execute(
        f"SELECT meter_category, sum(cost) FILTER (WHERE application = ?) AS un, "
        f"sum(cost) FILTER (WHERE application = ?) AS nt, sum(cost) AS c FROM agg_daily WHERE {gap_where} "
        f"GROUP BY 1 ORDER BY c DESC, 1 LIMIT {n}", [UNATTRIBUTED, UNTAGGED, *gp]).fetchall()
    groups = int(con.execute(f"SELECT count(*) FROM (SELECT 1 FROM agg_daily WHERE {gap_where} "
                             f"GROUP BY meter_category)", gp).fetchall()[0][0])
    gaps = [{"key": r[0], "unattributed": _d(r[1]), "untagged": _d(r[2]), "cost": _d(r[3])} for r in rows]
    gap_total = st.get("unattributed", z)[0] + st.get("untagged", z)[0]
    def share(value: Decimal) -> Decimal | None:
        return None if total == 0 else value / total
    return {
        "total": total,
        "attributed": st.get("attributed", z)[0], "unattributed": st.get("unattributed", z)[0],
        "untagged": st.get("untagged", z)[0],
        "attributed_share": share(st.get("attributed", z)[0]),
        "application_count": st.get("attributed", z)[1],
        "environment_none": env_none, "environment_none_share": share(env_none),
        "gap_by_service": gaps, "gap_groups": groups,
        "gap_other": gap_total - sum((g["cost"] for g in gaps), ZERO),
        "definition": ("Attributed: one of the configured application tag keys has a value. Unattributed: the "
                       "resource has tags but none of those keys. Untagged: the resource has no tags at all."),
    }


def workload_families(con: DuckDBPyConnection, f: Filters) -> dict[str, Any]:
    from . import workloads
    w, p = f.where()
    rows = con.execute(f"SELECT meter_category, usage_month, sum(cost) FROM agg_daily WHERE {w} GROUP BY 1, 2", p).fetchall()
    return workloads.build((r[0], r[1], _d(r[2])) for r in rows)


def pricing(con: DuckDBPyConnection, f: Filters, n: int = 15) -> dict[str, Any]:
    """Observed price paid against the pay-as-you-go value of the same usage, plus what reference data is loaded.

    Only Usage rows with a known PayG price are compared; rows without one are counted, never valued at zero.
    ratio_paid = cost / PayG value over the same rows (1.0 means list price)."""
    n = max(1, min(int(n), 100))
    w, p = f.where()
    base = ("SUM(cost) AS cost, sum(cost_with_payg) AS cwp, sum(payg_equivalent) AS payg, "
            "sum(payg_missing_rows) AS missing, sum(contract_basis_rows) AS contract, sum(row_count) AS rows")

    def shape(r: Any) -> dict[str, Any]:
        cost, cwp, payg = _d(r[1]), _d(r[2]), _d(r[3])
        return {"key": r[0], "cost": cost, "cost_with_payg": cwp, "payg_equivalent": payg,
                "ratio_paid": None if payg == 0 else cwp / payg,
                "known_share": None if cost == 0 else cwp / cost,
                "missing_rows": int(r[4] or 0), "contract_rows": int(r[5] or 0), "rows": int(r[6] or 0)}

    uw = f"{w} AND charge_type = 'Usage'"
    by_service = [shape(r) for r in con.execute(
        f"SELECT meter_category, {base} FROM agg_daily WHERE {uw} GROUP BY 1 ORDER BY payg DESC NULLS LAST, 1 LIMIT {n}", p).fetchall()]
    groups = int(con.execute(f"SELECT count(*) FROM (SELECT 1 FROM agg_daily WHERE {uw} GROUP BY meter_category)", p).fetchall()[0][0])
    by_model = [shape(r) for r in con.execute(
        f"SELECT pricing_model, {base} FROM agg_daily WHERE {uw} GROUP BY 1 ORDER BY payg DESC NULLS LAST, 1", p).fetchall()]
    t = shape(("all", *con.execute(f"SELECT {base} FROM agg_daily WHERE {uw}", p).fetchall()[0]))
    ref = {
        "price_benchmarks": int(con.execute("SELECT count(*) FROM price_benchmarks").fetchall()[0][0]),
        "subscriptions_named": int(con.execute("SELECT count(*) FROM dim_subscription").fetchall()[0][0]),
        "subscriptions_in_data": int(con.execute("SELECT count(DISTINCT subscription_id) FROM agg_daily").fetchall()[0][0]),
    }
    return {"totals": t, "by_service": by_service, "service_groups": groups, "by_pricing_model": by_model,
            "reference": ref,
            "definition": ("Compares what was charged with the baseline-price value of the same usage (the contract unit "
                           "price unless the export carries a PayG price), over Usage rows "
                           "that have one. Rows without one are counted and left out, not valued at zero. "
                           "Reservation and savings-plan rows with a zero PayG price are valued at their contract unit "
                           "price. On the amortized basis the gap includes commitment discounts as well as negotiated rates.")}


SP_RECOMMENDATION_DEFINITION = (
    "An hourly purchase recommendation needs hourly usage for the same price-matched, plan-eligible meters, "
    "together with existing commitment coverage. Daily category averages cannot establish a safe hourly baseline. "
    "The monthly savings figures remain ceilings, not purchase recommendations.")


def _sp_recommendation(month: date) -> dict[str, Any]:
    return {"available": False, "month": month, "days": 0, "terms": {},
            "reason": "No hourly purchase recommendation: this export contains daily usage, not hourly "
                      "price-matched, plan-eligible demand. Check existing unused commitments before buying more.",
            "definition": SP_RECOMMENDATION_DEFINITION}


def savings_headroom(con: DuckDBPyConnection, f: Filters, n: int = 15, latest_month: bool = True) -> dict[str, Any]:
    """Upper bound on what the price sheet's savings-plan prices would save on current on-demand usage.

    Each on-demand usage row (monthly grain) is matched to the price sheet of its own month by meter id, offer and
    normalised unit (see units.py). Where exactly one On-Demand price and one SavingsPlan price exist, the saving is
    cost x (1 - savings plan price / on-demand price), never below zero. eligible = has a plan price; cheaper = that
    price is below the on-demand contract price (otherwise the saving is zero). Unmatched, ambiguous and
    no-price-sheet-month cost is reported separately and gets no saving. This assumes every eligible hour is covered;
    a real commitment is sized on a steady baseline, so treat it as a ceiling, not a recommendation.

    By default only the latest month in the range with on-demand usage is used, so the figures describe what is
    uncovered now rather than a sum over past months; `month` in the result names it."""
    from .units import unit_sql
    n = max(1, min(int(n), 100))
    w, p, ignored = _resource_where(f)
    un, ub = unit_sql("unit")
    sn, sb = unit_sql("unit_of_measure")
    have = int(con.execute("SELECT count(*) FROM price_sheet WHERE currency = ?", [f.currency]).fetchall()[0][0])
    months_loaded = [r[0] for r in con.execute(
        "SELECT DISTINCT effective_start FROM price_sheet WHERE currency = ? ORDER BY 1", [f.currency]).fetchall()]
    month = None
    if latest_month:
        month = con.execute(f"SELECT max(usage_month) FROM agg_resource_month WHERE {w} AND charge_type = 'Usage' "
                            "AND pricing_model = 'On Demand'", p).fetchall()[0][0]
        if month is not None:
            w, p, ignored = _resource_where(replace(f, start=month, end=_month_end(month)))
    base: dict[str, Any] = {"available": have > 0, "price_months": months_loaded, "filters_not_applied": ignored, "month": month}
    empty: dict[str, Any] = {"totals": None, "recommendation": None, "by_month": [], "by_service": [], "service_groups": 0, "by_meter": [],
             "meter_groups": 0, "definition": None}
    if f.combined:
        return {**base, **empty, "available": False, "reason": "Savings headroom compares prices within one currency, "
                "so it is not available in the combined view. Choose a single currency."}
    if have == 0:
        return {**base, **empty, "reason": f"No price sheet is loaded for {f.currency}. Import one with azfinops-pricesheet."}
    sql = f"""
WITH r AS (
  SELECT usage_month, meter_category, coalesce(meter_id, '') AS meter_id,
         any_value(meter_name) AS meter_name, unit,
         COALESCE(offer_id, '') AS offer_id, sum(cost) AS cost
  FROM agg_resource_month
  WHERE {w} AND charge_type = 'Usage' AND pricing_model = 'On Demand'
  GROUP BY usage_month, meter_category, meter_id, unit, COALESCE(offer_id, '')),
rb AS (SELECT *, {un} AS blk, {ub} AS base FROM r),
s AS (SELECT effective_start, meter_id, offer_id, price_type, term, unit_price / {sn} AS ppb, {sb} AS base
      FROM price_sheet WHERE currency = ? AND effective_start IN (SELECT DISTINCT usage_month FROM r)),
od AS (SELECT effective_start, meter_id, offer_id, base, count(*) AS cnt, min(ppb) AS ppb
       FROM s WHERE price_type = 'Consumption' GROUP BY ALL),
sp1 AS (SELECT effective_start, meter_id, offer_id, base, count(*) AS cnt, min(ppb) AS ppb
        FROM s WHERE price_type = 'SavingsPlan' AND term = 'P1Y' GROUP BY ALL),
sp3 AS (SELECT effective_start, meter_id, offer_id, base, count(*) AS cnt, min(ppb) AS ppb
        FROM s WHERE price_type = 'SavingsPlan' AND term = 'P3Y' GROUP BY ALL),
j AS (
  SELECT rb.usage_month, rb.meter_category, rb.meter_id, rb.meter_name, rb.cost,
    CASE WHEN rb.usage_month NOT IN (SELECT DISTINCT effective_start FROM s) THEN 'no_sheet'
         WHEN od.cnt IS NULL THEN 'unmatched'
         WHEN od.cnt > 1 OR od.ppb IS NULL OR od.ppb <= 0 THEN 'ambiguous'
         ELSE 'matched' END AS status,
    (od.cnt = 1 AND od.ppb > 0 AND sp1.cnt = 1) AS has1,
    (od.cnt = 1 AND od.ppb > 0 AND sp3.cnt = 1) AS has3,
    (od.cnt = 1 AND od.ppb > 0 AND sp1.cnt = 1 AND sp1.ppb < od.ppb) AS better1,
    (od.cnt = 1 AND od.ppb > 0 AND sp3.cnt = 1 AND sp3.ppb < od.ppb) AS better3,
    CASE WHEN od.cnt = 1 AND od.ppb > 0 AND sp1.cnt = 1
         THEN CAST(greatest(0, rb.cost * (1 - sp1.ppb / od.ppb)) AS DECIMAL(38,18)) ELSE 0 END AS sav1,
    CASE WHEN od.cnt = 1 AND od.ppb > 0 AND sp3.cnt = 1
         THEN CAST(greatest(0, rb.cost * (1 - sp3.ppb / od.ppb)) AS DECIMAL(38,18)) ELSE 0 END AS sav3
  FROM rb
  LEFT JOIN od  ON od.effective_start = rb.usage_month AND od.meter_id = rb.meter_id AND od.offer_id = rb.offer_id AND od.base = rb.base
  LEFT JOIN sp1 ON sp1.effective_start = rb.usage_month AND sp1.meter_id = rb.meter_id AND sp1.offer_id = rb.offer_id AND sp1.base = rb.base
  LEFT JOIN sp3 ON sp3.effective_start = rb.usage_month AND sp3.meter_id = rb.meter_id AND sp3.offer_id = rb.offer_id AND sp3.base = rb.base)
SELECT usage_month, meter_category, meter_id, any_value(meter_name), status, sum(cost),
       sum(cost) FILTER (WHERE has1), sum(cost) FILTER (WHERE has3), sum(sav1), sum(sav3),
       sum(cost) FILTER (WHERE better1), sum(cost) FILTER (WHERE better3)
FROM j GROUP BY usage_month, meter_category, meter_id, status"""
    rows = con.execute(sql, [*p, f.currency]).fetchall()

    def blank() -> dict[str, Any]:
        return {"on_demand_cost": ZERO, "matched_cost": ZERO, "unmatched_cost": ZERO, "ambiguous_cost": ZERO,
                "no_sheet_cost": ZERO, "eligible_cost_1y": ZERO, "eligible_cost_3y": ZERO,
                "cheaper_cost_1y": ZERO, "cheaper_cost_3y": ZERO, "saving_1y": ZERO, "saving_3y": ZERO}

    def add(acc: dict[str, Any], status: str, cost: Decimal, e1: Decimal, e3: Decimal, s1: Decimal, s3: Decimal,
            b1: Decimal, b3: Decimal) -> None:
        acc["on_demand_cost"] += cost
        acc[{"matched": "matched_cost", "unmatched": "unmatched_cost", "ambiguous": "ambiguous_cost",
             "no_sheet": "no_sheet_cost"}[status]] += cost
        acc["eligible_cost_1y"] += e1; acc["eligible_cost_3y"] += e3; acc["saving_1y"] += s1; acc["saving_3y"] += s3
        acc["cheaper_cost_1y"] += b1; acc["cheaper_cost_3y"] += b3

    tot = blank()
    months: dict[date, dict[str, Any]] = {}
    svc: dict[str | None, dict[str, Any]] = {}
    mtr: dict[str, dict[str, Any]] = {}
    for month, cat, mid, name, status, cost, e1, e3, s1, s3, b1, b3 in rows:
        vals = (status, _d(cost), _d(e1), _d(e3), _d(s1), _d(s3), _d(b1), _d(b3))
        add(tot, *vals)
        add(months.setdefault(month, blank()), *vals)
        add(svc.setdefault(cat, blank()), *vals)
        m = mtr.setdefault(mid, {**blank(), "name": name if mid else "(missing meter ID)", "category": cat})
        add(m, *vals)
    tot["matched_share"] = None if tot["on_demand_cost"] == 0 else tot["matched_cost"] / tot["on_demand_cost"]
    by_service = sorted(({"key": k, **v} for k, v in svc.items()),
                        key=lambda r: (-r["saving_3y"], -r["on_demand_cost"], str(r["key"])))
    by_meter = sorted(({"meter_id": k, **v} for k, v in mtr.items()),
                      key=lambda r: (-r["saving_3y"], -r["on_demand_cost"], r["meter_id"]))
    return {**base, "reason": None, "totals": tot,
            "recommendation": _sp_recommendation(base["month"]) if base["month"] else None,
            "by_month": [{"month": k, **v} for k, v in sorted(months.items())],
            "by_service": by_service[:n], "service_groups": len(by_service),
            "by_meter": by_meter[:n], "meter_groups": len(by_meter),
            "definition": ("Upper bound. Each on-demand usage row is matched to the price sheet for its month by meter, offer "
                           "and billing unit; where one on-demand price and one savings-plan price exist, saving = cost x "
                           "(1 - savings plan price / on-demand price), never below zero. It assumes every eligible hour "
                           "is covered. A real savings plan is sized on a steady hourly baseline and this export is daily, "
                           "so actual savings are lower. Reservation prices are not compared. Cost without a meter ID "
                           "is included as unmatched (or no sheet for that month). Unmatched cost gets no saving.")}


def period_freshness(con: DuckDBPyConnection, f: Filters) -> list[dict[str, Any]]:
    """Coverage of imported dates, independent of filters that merely select a service or resource."""
    clauses = ["cost_kind = ?"]
    params: list[Any] = [f.cost_kind]
    if not f.combined:
        clauses.append("billing_currency = ?")
        params.append(f.currency)
    if f.billing_account_ids:
        clauses.append(f"billing_account_id IN ({','.join('?' * len(f.billing_account_ids))})")
        params.extend(f.billing_account_ids)
    rows = con.execute(f"""
        WITH base AS (SELECT * FROM aggregate_scope_freshness WHERE {' AND '.join(clauses)}),
        scopes AS (SELECT DISTINCT billing_account_id, billing_currency FROM base),
        bounds AS (
          SELECT coalesce(CAST(date_trunc('month', CAST(? AS DATE)) AS DATE), min(usage_month)) AS lo,
                 coalesce(CAST(date_trunc('month', CAST(? AS DATE)) AS DATE), max(usage_month)) AS hi,
                 min(usage_month) AS first_month, max(usage_month) AS last_month FROM base),
        months AS (
          SELECT CAST(unnest(generate_series(greatest(lo, first_month), least(hi, last_month),
                                              INTERVAL 1 MONTH)) AS DATE) AS month
          FROM bounds WHERE first_month IS NOT NULL
          UNION SELECT lo FROM bounds WHERE lo < first_month OR lo > last_month
          UNION SELECT hi FROM bounds WHERE hi < first_month OR hi > last_month)
        SELECT m.month, min(b.built_at), coalesce(sum(b.source_row_count), 0),
               bool_and(coalesce(b.period_complete, false)),
               list(DISTINCT s.billing_account_id ORDER BY s.billing_account_id)
                 FILTER (WHERE NOT coalesce(b.period_complete, false))
        FROM months m CROSS JOIN scopes s LEFT JOIN base b
          ON b.usage_month = m.month AND b.billing_account_id = s.billing_account_id
                                    AND b.billing_currency = s.billing_currency
        GROUP BY m.month ORDER BY m.month""", [*params, f.start, f.end]).fetchall()
    return [{"month": m, "built_at": built, "rows": int(n), "complete": bool(complete),
             "incomplete_accounts": accounts or []} for m, built, n, complete, accounts in rows]


def quality(con: DuckDBPyConnection, f: Filters) -> dict[str, Any]:
    w, p = f.where()
    attributed = con.execute(
        f"SELECT sum(cost) FILTER (WHERE application NOT IN ('(unattributed)', '(untagged)')), sum(cost), "
        f"bool_or(is_synthetic) FROM agg_daily WHERE {w}", p).fetchall()[0]
    total = _d(attributed[1])
    fresh = period_freshness(con, f)
    return {"synthetic": bool(attributed[2]),
            "attributed_share": None if total == 0 else _d(attributed[0]) / total,
            "months": fresh, "has_incomplete_month": any(not r["complete"] for r in fresh)}


def _account_metrics(con: DuckDBPyConnection, f: Filters) -> dict[tuple[str, str], dict[str, Any]]:
    """The card figures per enrollment in two grouped queries, instead of re-running totals, commitments and quality
    once per enrollment. Keyed by (enrollment, currency): outside the Combined view each enrollment uses its own
    currency, so the currency filter is dropped and the currency becomes part of the group. Matches what
    totals/commitments/quality return for one enrollment (tests/test_account_metrics.py checks this)."""
    w, p = f.where()
    if not f.combined:
        w, p = w.replace("billing_currency = ?", "1 = 1", 1), [p[0], *p[2:]]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for acct, ccy, total, usage, attributed, used_cost, unused_res, unused_all in con.execute(
            "SELECT billing_account_id, billing_currency, sum(cost), sum(cost) FILTER (WHERE charge_type = 'Usage'), "
            "sum(cost) FILTER (WHERE application NOT IN ('(unattributed)', '(untagged)')), "
            "sum(cost) FILTER (WHERE charge_type = 'Usage' AND pricing_model = 'Reservation'), "
            "sum(cost) FILTER (WHERE charge_type = 'UnusedReservation'), "
            "sum(cost) FILTER (WHERE charge_type IN ('UnusedReservation','UnusedSavingsPlan')) "
            f"FROM agg_daily WHERE {w} GROUP BY 1, 2", p).fetchall():
        t = _d(total)
        out[(acct, ccy)] = {"total_metric": t, "usage": _d(usage),
                            "attributed_share": None if t == 0 else _d(attributed) / t,
                            "_used_cost": _d(used_cost), "_unused_res": _d(unused_res), "_unused_all": _d(unused_all)}
    cov: dict[tuple[str, str], tuple[Decimal, Decimal, Decimal]] = {}
    if f.cost_kind == "amortized":
        for acct, ccy, covered, uncovered, benefit_cost in con.execute(f"""
            WITH s AS (SELECT * FROM agg_daily WHERE {w}),
            cats AS (SELECT DISTINCT billing_account_id AS a, billing_currency AS c, meter_category AS m FROM s
                     WHERE charge_type = 'Usage' AND pricing_model IN ('Reservation','SavingsPlan'))
            SELECT s.billing_account_id, s.billing_currency,
                   sum(payg_equivalent) FILTER (WHERE pricing_model IN ('Reservation','SavingsPlan')),
                   sum(payg_equivalent) FILTER (WHERE pricing_model = 'On Demand'),
                   sum(cost_with_payg) FILTER (WHERE pricing_model IN ('Reservation','SavingsPlan'))
            FROM s JOIN cats ON s.billing_account_id = cats.a AND s.billing_currency = cats.c AND s.meter_category = cats.m
            WHERE charge_type = 'Usage' AND COALESCE(publisher_type, '') <> 'Marketplace'
            GROUP BY 1, 2""", p).fetchall():
            cov[(acct, ccy)] = (_d(covered), _d(uncovered), _d(benefit_cost))
    for k, m in out.items():
        used_cost, unused_res, unused_all = m.pop("_used_cost"), m.pop("_unused_res"), m.pop("_unused_all")
        if f.cost_kind != "amortized":
            continue
        covered, uncovered, benefit_cost = cov.get(k, (ZERO, ZERO, ZERO))
        eligible = covered + uncovered
        m.update({"unused": unused_all, "discount_net": covered - benefit_cost - unused_all,
                  "utilization": None if used_cost + unused_res == 0 else used_cost / (used_cost + unused_res),
                  "coverage": None if eligible == 0 else covered / eligible})
    return out


def pay_models(con: DuckDBPyConnection, f: Filters) -> dict[str, list[dict[str, Any]]]:
    """Usage cost by pricing model, overall and per enrollment, for the "How usage is paid" card. Any charge-type
    filter is replaced by Usage, as the card always shows usage."""
    w, p = f.where(exclude=("charge_types",))
    w += " AND charge_type = 'Usage'"
    overall = con.execute(f"SELECT pricing_model, sum(cost) AS c FROM agg_daily WHERE {w} GROUP BY 1 "
                          "ORDER BY c DESC, 1", p).fetchall()
    per = con.execute(f"SELECT billing_account_id, pricing_model, sum(cost) AS c FROM agg_daily WHERE {w} "
                      "GROUP BY 1, 2 ORDER BY 1, c DESC, 2", p).fetchall()
    return {"overall": [{"model": m, "cost": _d(c)} for m, c in overall],
            "by_account": [{"account": a, "model": m, "cost": _d(c)} for a, m, c in per]}


def by_account(con: DuckDBPyConnection, f: Filters) -> list[dict[str, Any]]:
    """Total cost per billing account (enrollment) in its own native currency, over the same filters as the headline
    total, so a single-currency view still lists every enrollment. Shares are given only when one currency is shown.
    In the combined view each row also carries `converted` (in the target currency, the part of the headline total this
    enrollment contributes); `partial` means some of its months have no saved rate and are left out of the headline."""
    w, p = f.where()
    w, p = w.replace("billing_currency = ?", "1 = 1", 1), [p[0], *p[2:]]
    native_t = "agg_daily"
    if f.combined:       # agg_daily is shadowed by the converted view here; read the real table for native amounts
        cat = str(con.execute("SELECT current_database()").fetchall()[0][0]).replace('"', '""')
        native_t = f'"{cat}".main.agg_daily'
    rows = con.execute(f"SELECT billing_account_id, billing_currency, sum(cost), sum(row_count) FROM {native_t} "
                       f"WHERE {w} GROUP BY 1, 2 ORDER BY 3 DESC, 1", p).fetchall()
    conv: dict[str, tuple[Decimal, int]] = {}
    if f.combined:
        cw, cp = f.where()
        for acct, c, n in con.execute(
                f"SELECT billing_account_id, sum(cost), sum(row_count) FROM agg_daily WHERE {cw} GROUP BY 1", cp).fetchall():
            conv[acct] = (_d(c), int(n))
    conv_total = sum((v[0] for v in conv.values()), ZERO)
    one = len({r[1] for r in rows}) == 1
    total_native = sum((_d(r[2]) for r in rows), ZERO)
    metrics = _account_metrics(con, f)
    out = []
    for acct, ccy, cost, n in rows:
        row: dict[str, Any] = {"account": acct, "currency": ccy, "cost": _d(cost), "rows": int(n),
                               "converted": None, "partial": False, "share": None}
        if f.combined:
            c_cost, c_rows = conv.get(acct, (ZERO, 0))
            row["converted"] = c_cost
            row["partial"] = c_rows < int(n)
            row["share"] = float(c_cost / conv_total) if conv_total != 0 else None
        elif one and total_native != 0:
            row["share"] = float(_d(cost) / total_native)
        # The same figures the cards show, for this enrollment alone: in the combined view in the target currency,
        # otherwise in the enrollment's own currency. All enrollments come from one grouped pass (see _account_metrics).
        row.update({"metrics_currency": f.currency if f.combined else ccy, "total_metric": ZERO, "usage": ZERO,
                    "attributed_share": None, "unused": None, "utilization": None, "coverage": None, "discount_net": None})
        row.update(metrics.get((acct, f.currency if f.combined else ccy), {}))
        out.append(row)
    return out


def by_account_month(con: DuckDBPyConnection, f: Filters) -> list[dict[str, Any]]:
    """Cost per enrollment per month (same currency rules as by_account)."""
    w, p = f.where()
    if not f.combined:
        w, p = w.replace("billing_currency = ?", "1 = 1", 1), [p[0], *p[2:]]
    rows = con.execute(f"SELECT billing_account_id, billing_currency, usage_month, sum(cost) FROM agg_daily "
                       f"WHERE {w} GROUP BY 1, 2, 3 ORDER BY 1, 2, 3", p).fetchall()
    return [{"account": r[0], "currency": r[1], "month": r[2], "cost": _d(r[3])} for r in rows]


def _month_before(m: date) -> date:
    return date(m.year - 1, 12, 1) if m.month == 1 else date(m.year, m.month - 1, 1)


def _month_end(m: date) -> date:
    nxt = date(m.year + 1, 1, 1) if m.month == 12 else date(m.year, m.month + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


MOVERS_DEFINITION = ("Change in cost between two whole calendar months, per value of the dimension: the later month "
                     "minus the earlier one. When one month is selected it is compared with the month before it; "
                     "otherwise the last two months in the range are compared. Signed: credits count as negatives.")


def movers(con: DuckDBPyConnection, f: Filters, dimension: str = "service", n: int = 5) -> dict[str, Any]:
    """The biggest increases and decreases between two whole months, by one dimension."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}")
    col = DIMENSIONS[dimension]
    n = max(1, min(int(n), 20))
    w, p = f.where()
    months = [r[0] for r in con.execute(f"SELECT DISTINCT usage_month FROM agg_daily WHERE {w} ORDER BY 1", p).fetchall()]
    if not months:
        return {"available": False, "reason": "No cost in this selection.", "dimension": dimension,
                "increases": [], "decreases": [], "definition": MOVERS_DEFINITION}
    m1 = months[-1]
    m0 = months[-2] if len(months) >= 2 else _month_before(m1)
    # Both months are compared whole, whatever days the range starts or ends on.
    g = replace(f, start=m0, end=_month_end(m1))
    fresh = {r["month"]: r["complete"] for r in period_freshness(con, g)}
    if not all(fresh.get(m, False) for m in (m0, m1)):
        return {"available": False, "reason": "Changes need two complete months for every selected enrollment "
                "and currency.", "dimension": dimension, "month_from": m0, "month_to": m1,
                "complete_from": fresh.get(m0), "complete_to": fresh.get(m1),
                "increases": [], "decreases": [], "definition": MOVERS_DEFINITION}
    w2, p2 = g.where()
    rows = con.execute(
        f"SELECT {col}, coalesce(sum(cost) FILTER (WHERE usage_month = ?), 0), "
        f"coalesce(sum(cost) FILTER (WHERE usage_month = ?), 0) FROM agg_daily "
        f"WHERE {w2} AND usage_month IN (?, ?) GROUP BY 1", [m0, m1, *p2, m0, m1]).fetchall()
    items = [{"key": k, "label": None, "cost_from": _d(c0), "cost_to": _d(c1), "change": _d(c1) - _d(c0),
              "share": None if _d(c0) == 0 else float((_d(c1) - _d(c0)) / abs(_d(c0)))} for k, c0, c1 in rows]
    up = sorted((i for i in items if i["change"] > 0), key=lambda i: (-i["change"], str(i["key"])))[:n]
    down = sorted((i for i in items if i["change"] < 0), key=lambda i: (i["change"], str(i["key"])))[:n]
    t0 = sum((i["cost_from"] for i in items), ZERO)
    t1 = sum((i["cost_to"] for i in items), ZERO)
    return {"available": True, "reason": None, "dimension": dimension, "month_from": m0, "month_to": m1,
            "total_from": t0, "total_to": t1, "change": t1 - t0,
            "complete_from": fresh.get(m0), "complete_to": fresh.get(m1),
            "increases": up, "decreases": down, "definition": MOVERS_DEFINITION}


# Meter categories Azure's savings plan for compute can apply to. Categories where the data shows savings-plan usage
# are added to these, so a category named differently in the export is still counted once a plan covers it.
SP_CATEGORIES = ("Virtual Machines", "Azure App Service", "Container Instances", "Functions", "Azure Container Apps",
                 "Azure Spring Apps", "Azure Dedicated Host")

SP_HOURLY_DEFINITION = (
    "Per hour = cost per day / 24; the export is daily, so these are averages over each day, not hourly peaks. "
    "Not covered = on-demand usage cost in meter categories a savings plan for compute can apply to (the standard list "
    "plus any category with savings-plan usage in the selection). Spot, reservation-covered and Marketplace usage are "
    "left out. Category is an approximation: some meters in these categories (for example licences, or App Service "
    "plans other than Premium v3 and Isolated v2) are not eligible, so the figure can be high. Covered = amortized "
    "cost of usage billed through a savings plan; unused = UnusedSavingsPlan charges. The lowest daily average "
    "is not an hourly demand floor and cannot justify a purchase commitment.")


def sp_hourly(con: DuckDBPyConnection, f: Filters) -> dict[str, Any]:
    """Savings-plan-eligible usage per hour that is not covered, now and over recent days, with what plans cover."""
    w, p = f.where(exclude=("charge_types",))
    seen = [r[0] for r in con.execute(
        f"SELECT DISTINCT meter_category FROM agg_daily WHERE {w} AND charge_type = 'Usage' "
        "AND pricing_model = 'SavingsPlan' AND meter_category IS NOT NULL ORDER BY 1", p).fetchall()]
    cats = sorted(set(SP_CATEGORIES) | set(seen))
    ph = ",".join("?" * len(cats))
    days = con.execute(f"""
        SELECT usage_date,
               coalesce(sum(cost) FILTER (WHERE charge_type = 'Usage' AND pricing_model = 'On Demand'
                        AND meter_category IN ({ph}) AND COALESCE(publisher_type, '') <> 'Marketplace'), 0),
               coalesce(sum(cost) FILTER (WHERE charge_type = 'Usage' AND pricing_model = 'SavingsPlan'), 0),
               coalesce(sum(cost) FILTER (WHERE charge_type = 'UnusedSavingsPlan'), 0)
        FROM agg_daily WHERE {w} GROUP BY 1 ORDER BY 1""", [*cats, *p]).fetchall()
    base: dict[str, Any] = {"definition": SP_HOURLY_DEFINITION, "categories": cats, "categories_seen": seen}
    if not days:
        return {**base, "available": False, "reason": "No cost in this selection."}
    hr = Decimal(24)
    unc = [(d, _d(u) / hr) for d, u, _c, _x in days]
    last_day = days[-1][0]
    week = [x for d, x in unc if (last_day - d).days < 7]
    month = [x for d, x in unc if (last_day - d).days < 30]
    cov7 = [_d(c) / hr for d, _u, c, _x in days if (last_day - d).days < 7]
    unused7 = [_d(x) / hr for d, _u, _c, x in days if (last_day - d).days < 7]
    by_cat = con.execute(f"""
        SELECT meter_category, sum(cost) AS c FROM agg_daily
        WHERE {w} AND usage_date > ? AND charge_type = 'Usage' AND pricing_model = 'On Demand'
          AND meter_category IN ({ph}) AND COALESCE(publisher_type, '') <> 'Marketplace'
        GROUP BY 1 ORDER BY c DESC, 1""", [*p, last_day - timedelta(days=7), *cats]).fetchall()
    low_day, low = min(((d, x) for d, x in unc if (last_day - d).days < 30), key=lambda t: (t[1], t[0]))
    def avg(values: list[Decimal]) -> Decimal | None:
        return sum_decimal(values) / len(values) if values else None
    return {**base, "available": True, "reason": None, "last_day": last_day,
            "uncovered_last_day": unc[-1][1], "uncovered_7d": avg(week), "uncovered_30d": avg(month),
            "baseline_30d": low, "baseline_day": low_day, "days_7d": len(week), "days_30d": len(month),
            "covered_7d": avg(cov7), "unused_7d": avg(unused7),
            "by_category_7d": [{"category": c, "per_hour": _d(v) / (hr * len(week))} for c, v in by_cat],
            "daily": [{"day": d, "uncovered": x, "covered": _d(c) / hr} for (d, x), (_d0, _u, c, _x) in zip(unc, days)]}


SUMMARY_DEFINITION = (
    "All dates with data for the selected basis, currency and filters (the period buttons do not apply). Latest "
    "complete month: the newest month with every day loaded, against the month before. Subscriptions and resources: "
    "distinct IDs with cost. Trends: average cost per day over the last 30, 60 or 90 days with data against the same "
    "number of days just before; shown only when there are twice that many days. Top 3 service share: the three "
    "largest meter categories' share of the total.")


def _summary_rollup(con: DuckDBPyConnection, f: Filters) -> tuple[
    tuple[Any, ...], list[tuple[date, Decimal]], list[tuple[str | None, Decimal]]
]:
    w, p = replace(f, start=None, end=None).where()
    rows = con.execute(
        "SELECT usage_date, meter_category, sum(cost), sum(row_count) "
        f"FROM agg_daily WHERE {w} GROUP BY 1, 2", p).fetchall()
    if not rows:
        return (None, None, None, None, 0), [], []
    daily: dict[date, list[Decimal]] = {}
    services: dict[str | None, list[Decimal]] = {}
    for day, service, cost, _count in rows:
        daily.setdefault(day, []).append(_d(cost))
        services.setdefault(service, []).append(_d(cost))
    days = [(day, sum_decimal(values)) for day, values in sorted(daily.items())]
    ranked = [(service, sum_decimal(values)) for service, values in services.items()]
    ranked.sort(key=lambda r: (r[0] is None, r[0] or ""))
    ranked.sort(key=lambda r: r[1], reverse=True)
    source, native_where = _summary_native_source(con, f, "agg_daily", w)
    subscriptions = con.execute(f"SELECT count(DISTINCT subscription_id) FROM {source} WHERE {native_where}",
                                p).fetchall()[0][0]
    head = (sum_decimal(cost for _day, cost in days), sum(int(r[3]) for r in rows),
            days[0][0], days[-1][0], subscriptions)
    return head, days, ranked[:3]


def _summary_native_source(con: DuckDBPyConnection, f: Filters, table: str, where: str) -> tuple[str, str]:
    if not f.combined:
        return table, where
    cat = str(con.execute("SELECT current_database()").fetchall()[0][0]).replace('"', '""')
    return f'"{cat}".main.{table}', where.replace(
        "billing_currency = ?",
        "(billing_currency = ? OR (billing_currency, usage_month) IN "
        "(SELECT from_ccy, usage_month FROM fx_rate))", 1)


def summary(con: DuckDBPyConnection, f: Filters) -> dict[str, Any]:
    """The headline strip: totals over all dates, the latest complete month, counts, trends and service concentration."""
    g = replace(f, start=None, end=None)
    head, days, svc = _summary_rollup(con, g)
    if head[2] is None:
        return {"available": False, "reason": "No cost for these filters.", "definition": SUMMARY_DEFINITION}
    total, rows, first, last, subs = _d(head[0]), int(head[1] or 0), head[2], head[3], int(head[4] or 0)
    rw, rp, _ign = _resource_where(g)
    resource_source, rw = _summary_native_source(con, g, "agg_resource_month", rw)
    resources = con.execute(f"SELECT count(DISTINCT resource_id) FROM {resource_source} WHERE {rw} "
                            "AND resource_id IS NOT NULL", rp).fetchall()[0][0]
    month_days: dict[date, int] = {}
    month_cost: dict[date, Decimal] = {}
    for d, c in days:
        m = date(d.year, d.month, 1)
        month_days[m] = month_days.get(m, 0) + 1
        month_cost[m] = month_cost.get(m, ZERO) + _d(c)
    complete = [r["month"] for r in period_freshness(con, g) if r["complete"] and r["month"] in month_cost]
    latest = None
    if complete:
        m = complete[-1]
        pm = _month_before(m)
        prev = month_cost.get(pm) if pm in complete else None
        latest = {"month": m, "cost": month_cost[m], "previous_month": pm if prev is not None else None,
                  "previous_cost": prev, "change": None if prev in (None, ZERO) else float((month_cost[m] - prev) / abs(prev))}
    daily = [_d(c) for _d0, c in days]
    trends = []
    for n in (30, 60, 90):
        if len(daily) >= 2 * n and all(date(d.year, d.month, 1) in complete for d, _ in days[-2 * n:]):
            now = sum(daily[-n:], ZERO) / n
            before = sum(daily[-2 * n:-n], ZERO) / n
            trends.append({"days": n, "available": True, "per_day": now, "previous_per_day": before,
                           "change": None if before == 0 else float((now - before) / abs(before)), "days_needed": 2 * n})
        else:
            trends.append({"days": n, "available": False, "per_day": None, "previous_per_day": None, "change": None,
                           "days_needed": 2 * n})
    top = [{"service": k, "cost": _d(c), "share": None if total == 0 else float(_d(c) / total)} for k, c in svc]
    return {"available": True, "reason": None, "total": total, "first_day": first, "last_day": last,
            "days_with_data": len(daily), "billing_records": rows, "subscriptions": subs, "resources": int(resources or 0),
            "latest_month": latest, "trends": trends, "top_services": top,
            "top3_share": None if total == 0 else float(sum_decimal(cost for _service, cost in svc) / total),
            "definition": SUMMARY_DEFINITION}


ANALYSIS_DEFINITION = (
    "Signed cost per day or month for the selected basis, currency, dates and filters, split by one dimension: the "
    "largest values over the whole period are shown on their own and the rest are added up as Others. Credits and "
    "refunds count as negatives. Accumulated is a running total of the daily figures.")


def _days(a: date, b: date) -> list[date]:
    return [date.fromordinal(o) for o in range(a.toordinal(), b.toordinal() + 1)]


def _months(a: date, b: date) -> list[date]:
    out, m = [], date(a.year, a.month, 1)
    while m <= b:
        out.append(m)
        m = date(m.year + 1, 1, 1) if m.month == 12 else date(m.year, m.month + 1, 1)
    return out


def analysis(con: DuckDBPyConnection, f: Filters, dimension: str = "service", grain: str = "day", n: int = 8) -> dict[str, Any]:
    """Cost over time split by one dimension, for the Cost analysis chart: top `n` series plus Others."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}")
    if grain not in ("day", "month"):
        raise ValueError("grain must be day or month")
    n = max(1, min(int(n), 12))
    col, per = DIMENSIONS[dimension], {"day": "usage_date", "month": "usage_month"}[grain]
    w, p = f.where()
    rows = con.execute(f"SELECT {per}, {col}, sum(cost) FROM agg_daily WHERE {w} GROUP BY 1, 2", p).fetchall()
    span = con.execute(f"SELECT min(usage_date), max(usage_date) FROM agg_daily WHERE {w}", p).fetchall()[0]
    base = {"dimension": dimension, "grain": grain, "definition": ANALYSIS_DEFINITION}
    if not rows:
        return {**base, "available": False, "reason": "No cost in this selection.", "periods": [], "series": [],
                "totals": [], "total": ZERO, "grouped": 0, "first_day": None, "last_day": None}
    first, last = span
    periods = _days(first, last) if grain == "day" else _months(first, last)
    idx = {d: i for i, d in enumerate(periods)}
    by_key: dict[Any, list[Decimal]] = {}
    for d, k, c in rows:
        by_key.setdefault(k, [ZERO] * len(periods))[idx[d]] += _d(c)
    ranked = sorted(by_key.items(), key=lambda kv: (-sum(kv[1], ZERO), str(kv[0])))
    series: list[dict[str, Any]] = [{"key": None if k is None else str(k), "label": None, "values": v, "total": sum_decimal(v)} for k, v in ranked[:n]]
    if len(ranked) > n:
        rest = [sum((v[i] for _k, v in ranked[n:]), ZERO) for i in range(len(periods))]
        series.append({"key": OTHER, "label": None, "values": rest, "total": sum(rest, ZERO)})
    totals = [sum((s["values"][i] for s in series), ZERO) for i in range(len(periods))]
    return {**base, "available": True, "reason": None, "periods": periods, "series": series, "totals": totals,
            "total": sum(totals, ZERO), "grouped": max(0, len(ranked) - n), "first_day": first, "last_day": last}


def previous_range(start: date, end: date) -> tuple[date, date]:
    """The period before start..end: the previous month(s) when the range is whole months, otherwise the same number
    of days immediately before."""
    whole = start.day == 1 and end == _month_end(date(end.year, end.month, 1))
    if whole:
        k = (end.year - start.year) * 12 + end.month - start.month + 1
        m = date(start.year, start.month, 1)
        for _ in range(k):
            m = _month_before(m)
        last = m
        for _ in range(k - 1):
            last = date(last.year + 1, 1, 1) if last.month == 12 else date(last.year, last.month + 1, 1)
        return m, _month_end(last)
    days = end.toordinal() - start.toordinal() + 1
    return date.fromordinal(start.toordinal() - days), date.fromordinal(start.toordinal() - 1)


OTHER = "__other__"
FLOW_DEFINITION = (
    "Cost from each value of the first level, through the second, to the third, for the selected period, basis and "
    "currency. Each level shows its largest values by cost; the rest are grouped as Other. A Sankey cannot draw a "
    "negative flow, so any path (combination of the three values) whose net cost is below zero, usually credits or "
    "refunds, is left out of the drawing and reported as credits; drawn + credits = total.")


def flow(con: DuckDBPyConnection, f: Filters, levels: tuple[str, ...] = ("billing_account", "service", "application"),
         n: int = 8) -> dict[str, Any]:
    """Paths of cost through two or three dimensions, for a Sankey: top `n` values per level, the rest as Other."""
    if not 2 <= len(levels) <= 3 or len(set(levels)) != len(levels) or any(lv not in DIMENSIONS for lv in levels):
        raise ValueError("levels must be 2 or 3 different dimensions")
    n = max(2, min(int(n), 15))
    w, p = f.where()
    cols = ", ".join(DIMENSIONS[lv] for lv in levels)
    rows = con.execute(f"SELECT {cols}, sum(cost) FROM agg_daily WHERE {w} GROUP BY ALL", p).fetchall()
    base = {"levels": list(levels), "definition": FLOW_DEFINITION}
    if not rows:
        return {**base, "available": False, "reason": "No cost in this selection.", "total": ZERO, "drawn": ZERO,
                "credits": ZERO, "paths": [], "grouped": [0] * len(levels)}
    keep: list[set[str | None]] = []
    grouped = []
    for i in range(len(levels)):
        tot: dict[Any, Decimal] = {}
        for r in rows:
            tot[r[i]] = tot.get(r[i], ZERO) + _d(r[-1])
        ranked = sorted(tot.items(), key=lambda kv: (-kv[1], str(kv[0])))
        keep.append({k for k, _v in ranked[:n]})
        grouped.append(max(0, len(ranked) - n))
    paths: dict[tuple[str | None, ...], Decimal] = {}
    for r in rows:
        key = tuple((r[i] if r[i] in keep[i] else OTHER) for i in range(len(levels)))
        paths[key] = paths.get(key, ZERO) + _d(r[-1])
    total = sum(paths.values(), ZERO)
    drawn = [{"keys": [None if k is None else str(k) for k in key], "value": v}
             for key, v in sorted(paths.items(), key=lambda kv: (-kv[1], [str(x) for x in kv[0]])) if v > 0]
    credits = sum((v for v in paths.values() if v <= 0), ZERO)
    return {**base, "available": bool(drawn), "reason": None if drawn else "Nothing to draw: every path nets to zero or less.",
            "total": total, "drawn": total - credits, "credits": credits, "paths": drawn, "grouped": grouped}


RESOURCE_CHANGES_DEFINITION = (
    "Compares each resource's usage in two whole months: the later month and the one before it. Decommissioned: it "
    "was running at the end of the earlier month and stopped before the end of the later one (or has no usage in it). "
    "Resized: it ran at the end of both months but on a different meter, which for VMs, disks and databases is the "
    "size or tier (the meter with the most cost among those still running at month end). Saving is a run-rate: cost "
    "per day of the meters running at the end of each month, times the days in the later month, so a change made "
    "mid-month counts in full. Usage rows only; amortized cost includes the share of any reservation or savings plan. "
    "A resource that was deleted and re-created under a new ID shows as decommissioned, and a size change that keeps "
    "the meter (for example more vCores billed on the same meter) is not detected.")

_KINDS = ("decommissioned", "downsized", "upsized")


def resource_changes(con: DuckDBPyConnection, f: Filters, n: int = 200) -> dict[str, Any]:
    """Resources decommissioned or resized between two whole months, with a run-rate saving each (see definition)."""
    n = max(1, min(int(n), 1000))
    w, p, ignored = _resource_where(f)
    base = {"definition": RESOURCE_CHANGES_DEFINITION, "filters_not_applied": ignored,
            "totals": {k: {"count": 0, "saving": ZERO} for k in _KINDS}, "net_saving": ZERO, "by_account": [], "rows": []}
    months = [r[0] for r in con.execute(
        f"SELECT DISTINCT usage_month FROM agg_resource_month WHERE {w} ORDER BY 1", p).fetchall()]
    if not months:
        return {**base, "available": False, "reason": "No usage in this selection."}
    m1 = months[-1]
    m0 = months[-2] if len(months) >= 2 else _month_before(m1)
    w2, p2, _ = _resource_where(f, dates=False)
    w2 += " AND usage_month IN (?, ?) AND charge_type = 'Usage' AND resource_id IS NOT NULL"
    p2 = [*p2, m0, m1]
    have = con.execute(f"SELECT usage_month, count(*) FILTER (WHERE active_days IS NULL), count(*) "
                       f"FROM agg_resource_month WHERE {w2} GROUP BY 1", p2).fetchall()
    found = {m: (missing, total) for m, missing, total in have}
    if m0 not in found:
        return {**base, "available": False, "month_from": m0, "month_to": m1,
                "reason": f"No usage in {m0:%b %Y} to compare with."}
    if any(missing for missing, _t in found.values()):
        return {**base, "available": False, "month_from": m0, "month_to": m1,
                "reason": "The per-resource figures predate this feature. Stop the server and run azfinops-aggregate once."}
    complete = {r["month"]: r["complete"] for r in period_freshness(
        con, replace(f, start=m0, end=_month_end(m1)))}
    if not all(complete.get(m, False) for m in (m0, m1)):
        return {**base, "available": False, "month_from": m0, "month_to": m1,
                "complete_from": complete.get(m0), "complete_to": complete.get(m1),
                "reason": "Resource changes need two complete months for every selected enrollment and currency."}
    rows = con.execute(f"""
        WITH g AS (   -- one row per resource, month and meter
          SELECT usage_month AS m, resource_id, meter_category, meter_name,
                 max(resource_name) AS rn, max(subscription_id) AS sub, max(resource_group) AS rg,
                 max(billing_account_id) AS acct, sum(cost) AS cost, max(last_date) AS last_d,
                 greatest(max(active_days), 1) AS days
          FROM agg_resource_month WHERE {w2} GROUP BY 1, 2, 3, 4),
        ends AS (SELECT m, max(last_d) AS data_end FROM g GROUP BY 1),
        res AS (SELECT m, resource_id, max(last_d) AS res_last, sum(cost) AS res_cost FROM g GROUP BY 1, 2),
        running AS (   -- the meters still in use on the resource's last day in the month
          SELECT g.* FROM g JOIN res ON g.m = res.m AND g.resource_id = res.resource_id AND g.last_d = res.res_last),
        rate AS (
          SELECT m, resource_id, CAST(sum(CAST(cost AS DOUBLE) / days) AS DECIMAL(38, 6)) AS rate, arg_max(meter_name, cost) AS sku,
                 arg_max(meter_category, cost) AS cat, max(rn) AS rn, max(sub) AS sub, max(rg) AS rg, max(acct) AS acct
          FROM running GROUP BY 1, 2)
        SELECT b.resource_id, coalesce(a.rn, b.rn), coalesce(a.sub, b.sub), coalesce(a.rg, b.rg),
               coalesce(a.acct, b.acct), b.cat, b.sku, a.sku, b.rate, a.rate, r0.res_cost, r1.res_cost, r1.res_last,
               CASE WHEN a.resource_id IS NULL OR r1.res_last < e1.data_end - INTERVAL 1 DAY THEN 'decommissioned'
                    ELSE 'resized' END AS kind
        FROM rate b
        JOIN res r0 ON r0.m = b.m AND r0.resource_id = b.resource_id
        JOIN ends e0 ON e0.m = b.m
        CROSS JOIN (SELECT data_end FROM ends WHERE m = ?) e1
        LEFT JOIN rate a ON a.m = ? AND a.resource_id = b.resource_id
        LEFT JOIN res r1 ON r1.m = ? AND r1.resource_id = b.resource_id
        WHERE b.m = ? AND r0.res_last >= e0.data_end - INTERVAL 1 DAY
          AND (a.resource_id IS NULL OR r1.res_last < e1.data_end - INTERVAL 1 DAY OR a.sku IS DISTINCT FROM b.sku)
        """, [*p2, m1, m1, m1, m0]).fetchall()
    days1 = (_month_end(m1) - m1).days + 1
    items = []
    for rid, name, sub, rg, acct, cat, sku0, sku1, r0, r1, c0, c1, last1, kind in rows:
        r0, r1 = _d(r0), (ZERO if kind == "decommissioned" else _d(r1))
        saving = (r0 - r1) * days1
        if kind == "resized":
            kind = "downsized" if saving > 0 else "upsized"
        items.append({"kind": kind, "resource_id": rid, "resource_name": name, "subscription_id": sub,
                      "subscription": None, "resource_group": rg, "account": acct, "service": cat,
                      "sku_from": sku0, "sku_to": None if kind == "decommissioned" else sku1,
                      "cost_from": _d(c0), "cost_to": _d(c1), "rate_from": r0, "rate_to": r1,
                      "saving": saving, "stopped_on": last1 if kind == "decommissioned" else None})
    totals: dict[str, dict[str, Any]] = {k: {"count": 0, "saving": ZERO} for k in _KINDS}
    acct_net: dict[str | None, Decimal] = {}
    for i in items:
        totals[i["kind"]]["count"] += 1
        totals[i["kind"]]["saving"] += i["saving"]
        acct_net[i["account"]] = acct_net.get(i["account"], ZERO) + i["saving"]
    items.sort(key=lambda i: (-abs(i["saving"]), str(i["resource_id"])))
    shown = {k: [i for i in items if i["kind"] == k][:n] for k in _KINDS}
    return {**base, "available": True, "reason": None, "month_from": m0, "month_to": m1, "days_to": days1,
            "complete_from": complete.get(m0), "complete_to": complete.get(m1),
            "totals": totals, "net_saving": sum((t["saving"] for t in totals.values()), ZERO),
            "by_account": [{"account": a, "saving": v} for a, v in sorted(acct_net.items(), key=lambda x: str(x[0]))],
            "rows": shown["decommissioned"] + shown["downsized"] + shown["upsized"]}


OVERVIEW_TOPS = ("subscription", "service", "resource_group", "application")


def overview(con: DuckDBPyConnection, f: Filters, top_n: int = 10, tops: tuple[str, ...] = OVERVIEW_TOPS) -> dict[str, Any]:
    return {"totals": totals(con, f), "by_account": by_account(con, f), "by_account_month": by_account_month(con, f), "daily": series(con, f, "day"), "monthly": series(con, f, "month"),
            "daily_by_account": series_by_account(con, f, "day"), "monthly_by_account": series_by_account(con, f, "month"),
            "top": {d: top(con, f, d, top_n) for d in tops},
            "pay_models": pay_models(con, f),
            "commitments": commitments(con, f), "quality": quality(con, f)}
