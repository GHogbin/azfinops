"""FastAPI application factory. Everything under /api is read-only: GET over the aggregates, plus one POST
(/api/sql/run) that accepts a single SELECT statement and is guarded in sqlbench.py and db.py. The exchange-rate
endpoints (/api/fx/*) are the only other writes: they touch a JSON file, never the database, and need the same header."""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import accounts, fx, jobs, prefs, sqlbench
from .. import datamgmt as dm
from .. import queries as q
from ..config import Settings
from . import schemas as s
from .db import Database, Overloaded, Paused, QueryTimeout, StaleAggregates, request_timings

log = logging.getLogger("azfinops.api")
VERSION = "0.1.0"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")
SLOW_REQUEST_MS = 1000.0
MAX_FILTER_VALUES = 200
MAX_VALUE_LEN = 200

_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def _get_db(request: Request) -> Database:
    db = request.app.state.db
    if not isinstance(db, Database):
        raise TypeError("application database was not initialized")
    return db


Db = Annotated[Database, Depends(_get_db)]
ListParam = Annotated[list[str], Query(max_length=MAX_FILTER_VALUES)]


def _clean(values: list[str], name: str) -> tuple[str, ...]:
    for v in values:
        if len(v) > MAX_VALUE_LEN:
            raise ValueError(f"{name}: value longer than {MAX_VALUE_LEN} characters")
    return tuple(values)


def filters(
    cost_kind: Annotated[str, Query(description="actual or amortized; never mixed")],
    currency: Annotated[str, Query(pattern=r"^[A-Za-z]{3}$", description="native billing currency")],
    start: date | None = None,
    end: date | None = None,
    billing_account: ListParam = [],  # noqa: B006  (FastAPI copies query defaults)
    subscription: ListParam = [],  # noqa: B006
    application: ListParam = [],  # noqa: B006
    environment: ListParam = [],  # noqa: B006
    service: ListParam = [],  # noqa: B006
    region: ListParam = [],  # noqa: B006
    resource_group: ListParam = [],  # noqa: B006
    charge_type: ListParam = [],  # noqa: B006
    pricing_model: ListParam = [],  # noqa: B006
    publisher_type: ListParam = [],  # noqa: B006
    meter_subcategory: ListParam = [],  # noqa: B006
    combined: Annotated[bool, Query(description="show every currency converted into `currency`")] = False,
) -> q.Filters:
    return q.Filters(
        cost_kind, currency.upper(), start, end,
        billing_account_ids=_clean(billing_account, "billing_account"),
        subscription_ids=_clean(subscription, "subscription"),
        applications=_clean(application, "application"),
        environments=_clean(environment, "environment"),
        services=_clean(service, "service"),
        regions=_clean(region, "region"),
        resource_groups=_clean(resource_group, "resource_group"),
        charge_types=_clean(charge_type, "charge_type"),
        pricing_models=_clean(pricing_model, "pricing_model"),
        publisher_types=_clean(publisher_type, "publisher_type"),
        meter_subcategories=_clean(meter_subcategory, "meter_subcategory"), combined=combined)


F = Annotated[q.Filters, Depends(filters)]
router = APIRouter(prefix="/api")


@router.get("/health", response_model=s.Health)
def health(db: Db) -> dict[str, str]:
    with db.cursor() as con:
        con.execute("SELECT 1").fetchall()
    return {"status": "ok", "version": VERSION}


@router.get("/meta", response_model=s.Meta)
def meta(db: Db, request: Request) -> dict[str, Any]:
    with db.cursor(require_current=False) as con:
        scopes = con.execute(
            "SELECT cost_kind, billing_currency, min(usage_date), max(usage_date), sum(row_count), "
            "bool_or(is_synthetic) FROM agg_daily GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
        pairs = con.execute("SELECT DISTINCT billing_account_id, billing_currency FROM agg_daily ORDER BY 1, 2").fetchall()
        scope_months = [{"month": m, "cost_kind": kind, "account": a, "currency": c, "complete": complete}
                        for m, kind, a, c, complete in con.execute(
                            "SELECT usage_month, cost_kind, billing_account_id, billing_currency, period_complete "
                            "FROM aggregate_scope_freshness ORDER BY 1, 2, 3, 4").fetchall()]
    accounts_seen = sorted({a for a, _c in pairs if a is not None})
    currencies: dict[str, list[str]] = {}
    for a, c in pairs:
        if a is not None:
            currencies.setdefault(a, []).append(c)
    return {"scopes": [{"cost_kind": a, "currency": b, "first_date": c, "last_date": d, "rows": int(n),
                        "synthetic": bool(syn)} for a, b, c, d, n, syn in scopes],
            "billing_accounts": accounts_seen, "dimensions": sorted(q.DIMENSIONS),
            "billing_account_names": accounts.load(request.app.state.settings.data_dir),
            "billing_account_currencies": currencies, "recovery_required": db.recovery_required,
            "recovery_message": db.recovery_message if db.recovery_required else None, "scope_months": scope_months}


def _label_top(con: Any, t: dict[str, Any]) -> None:
    if t["dimension"] == "subscription":
        names = q.subscription_labels(con, [r["key"] for r in t["rows"]])
        for r in t["rows"]:
            r["label"] = names.get(r["key"])


@router.get("/overview", response_model=s.Overview)
def overview(f: F, db: Db, top_n: Annotated[int, Query(ge=1, le=50)] = 10,
             tops: Annotated[str, Query(pattern="^[a-z_,]{0,120}$")] = ",".join(q.OVERVIEW_TOPS)) -> dict[str, Any]:
    """`tops` names the ranked lists to include; the page asks for the rest separately, when they scroll into view."""
    wanted = tuple(t for t in tops.split(",") if t)
    if any(t not in q.OVERVIEW_TOPS for t in wanted):
        raise HTTPException(422, f"tops must be among {', '.join(q.OVERVIEW_TOPS)}")
    with db.cursor(f=f) as con:
        o = q.overview(con, f, top_n, wanted)
        for t in o["top"].values():
            _label_top(con, t)
    return o


@router.get("/cost/top", response_model=s.Top)
def cost_top(f: F, db: Db, dimension: Annotated[str, Query(pattern="^[a-z_]{1,40}$")],
             n: Annotated[int, Query(ge=1, le=50)] = 10) -> dict[str, Any]:
    """One ranked list with its enrollment split, as on the Overview."""
    if dimension not in q.DIMENSIONS:
        raise HTTPException(422, f"unknown dimension {dimension!r}")
    with db.cursor(f=f) as con:
        t = q.top(con, f, dimension, n)
        _label_top(con, t)
    return t


@router.get("/optimization/resource-changes", response_model=s.ResourceChanges)
def resource_changes(f: F, db: Db, n: Annotated[int, Query(ge=1, le=1000)] = 200) -> dict[str, Any]:
    """Resources decommissioned or resized between two whole months, with a run-rate saving each."""
    with db.cursor(f=f) as con:
        r = q.resource_changes(con, f, n)
        names = q.subscription_labels(con, [x["subscription_id"] for x in r["rows"]])
    for x in r["rows"]:
        x["subscription"] = names.get(x["subscription_id"]) if x["subscription_id"] else None
    return r


@router.get("/cost/flow", response_model=s.Flow)
def cost_flow(f: F, db: Db, request: Request,
              levels: Annotated[str, Query(pattern="^[a-z_,]{3,80}$")] = "billing_account,service,application",
              n: Annotated[int, Query(ge=2, le=15)] = 8) -> dict[str, Any]:
    """Cost flowing through two or three dimensions, for the Sankey on the Overview."""
    lv = tuple(x for x in levels.split(",") if x)
    try:
        with db.cursor(f=f) as con:
            r = q.flow(con, f, lv, n)
            keys = {i: {p["keys"][i] for p in r["paths"]} - {None, q.OTHER} for i in range(len(lv))}
            labels: dict[str, dict[str, str]] = {}
            for i, d in enumerate(lv):
                if d == "subscription":
                    labels[d] = q.subscription_labels(con, sorted(keys[i]))
                elif d == "billing_account":
                    names = accounts.load(request.app.state.settings.data_dir)
                    labels[d] = {k: names[k] for k in keys[i] if k in names}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {**r, "labels": labels}


@router.get("/overview/summary", response_model=s.Summary)
def overview_summary(f: F, db: Db) -> dict[str, Any]:
    """The Overview's headline strip, over all dates with data (the period does not apply)."""
    with db.cursor(f=f) as con:
        return q.summary(con, f)


@router.get("/cost/analysis", response_model=s.Analysis)
def cost_analysis(f: F, db: Db, request: Request,
                  dimension: Annotated[str, Query(pattern="^[a-z_]{1,40}$")] = "service",
                  grain: Annotated[str, Query(pattern="^(day|month)$")] = "day",
                  n: Annotated[int, Query(ge=1, le=12)] = 8,
                  compare: bool = False) -> dict[str, Any]:
    """Cost over time split by one dimension (the Cost analysis tab), optionally with the period before."""
    if dimension not in q.DIMENSIONS:
        raise HTTPException(422, f"unknown dimension {dimension!r}")
    with db.cursor(f=f) as con:
        r = q.analysis(con, f, dimension, grain, n)
        keys = [x["key"] for x in r["series"] if x["key"] not in (None, q.OTHER)]
        if dimension == "subscription":
            names = q.subscription_labels(con, keys)
        elif dimension == "billing_account":
            names = accounts.load(request.app.state.settings.data_dir)
        else:
            names = {}
        for x in r["series"]:
            x["label"] = names.get(x["key"]) if x["key"] else None
        if compare and r["available"]:
            start, end = f.start or r["first_day"], f.end or r["last_day"]
            p0, p1 = q.previous_range(start, end)
            current_fresh = q.period_freshness(con, replace(f, start=start, end=end))
            previous_fresh = q.period_freshness(con, replace(f, start=p0, end=p1))
            if current_fresh and previous_fresh and all(x["complete"] for x in [*current_fresh, *previous_fresh]):
                prev = q.analysis(con, replace(f, start=p0, end=p1), dimension, grain, 1)
                r["previous"] = {"start": p0, "end": p1, "periods": prev["periods"], "totals": prev["totals"],
                                 "total": prev["total"]}
            else:
                r["comparison_reason"] = "Comparison unavailable: every selected enrollment and currency "
                r["comparison_reason"] += "needs complete imported months for both periods."
    return r


@router.get("/budgets", response_model=s.Budgets)
def budgets_list(request: Request) -> dict[str, Any]:
    return {"budgets": prefs.budgets(request.app.state.settings.data_dir)}


@router.post("/budgets", response_model=s.Budgets)
def budgets_save(body: s.BudgetSave, request: Request, x_azfinops: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Set a monthly budget for "all" or one enrollment in a currency; an empty amount removes it."""
    _require_header(x_azfinops)
    try:
        return {"budgets": prefs.save_budget(request.app.state.settings.data_dir, body.scope, body.currency, body.amount)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/views", response_model=s.SavedViews)
def views_list(request: Request) -> dict[str, Any]:
    return {"views": prefs.views(request.app.state.settings.data_dir)}


@router.post("/views", response_model=s.SavedViews)
def views_save(body: s.ViewSave, request: Request, x_azfinops: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Save a named Cost analysis view (its URL query string); no query deletes the view of that name."""
    _require_header(x_azfinops)
    try:
        return {"views": prefs.save_view(request.app.state.settings.data_dir, body.name, body.query)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/cost/movers", response_model=s.Movers)
def cost_movers(f: F, db: Db, request: Request,
                dimension: Annotated[str, Query(pattern="^[a-z_]{1,40}$")] = "service",
                n: Annotated[int, Query(ge=1, le=20)] = 5) -> dict[str, Any]:
    if dimension not in q.DIMENSIONS:
        raise HTTPException(422, f"unknown dimension {dimension!r}")
    with db.cursor(f=f) as con:
        r = q.movers(con, f, dimension, n)
        rows = r["increases"] + r["decreases"]
        if dimension == "subscription":
            names = q.subscription_labels(con, [x["key"] for x in rows])
        elif dimension == "billing_account":
            names = accounts.load(request.app.state.settings.data_dir)
        else:
            names = {}
        for x in rows:
            x["label"] = names.get(x["key"]) if x["key"] else None
    return r


@router.get("/accounts/monthly", response_model=list[s.AccountMonth])
def accounts_monthly(f: F, db: Db) -> list[dict[str, Any]]:
    with db.cursor(f=f) as con:
        return q.by_account_month(con, f)


@router.get("/cost/series", response_model=list[s.SeriesPoint])
def cost_series(f: F, db: Db, grain: Annotated[str, Query(pattern="^(day|month)$")] = "day") -> list[dict[str, Any]]:
    with db.cursor(f=f) as con:
        return q.series(con, f, grain)


@router.get("/cost/explore", response_model=s.Explore)
def cost_explore(
    f: F, db: Db, request: Request,
    group_by: Annotated[list[str], Query(min_length=1, max_length=2)],
    sort: Annotated[str, Query(pattern="^(cost|key)$")] = "cost",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=q.MAX_PAGE_SIZE)] = 50,
) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        r = q.explore(con, f, group_by, sort, order == "desc", page, page_size)
        names: dict[str, str] = {}
        if "subscription" in group_by:
            i = group_by.index("subscription")
            names = q.subscription_labels(con, [row["keys"][i] for row in r["rows"]])
    acct_names = accounts.load(request.app.state.settings.data_dir) if "billing_account" in group_by else {}
    for row in r["rows"]:
        row["labels"] = [names.get(k) if g == "subscription" and k else acct_names.get(k) if g == "billing_account" and k else None
                         for g, k in zip(group_by, row["keys"], strict=True)]
    return r


@router.get("/applications", response_model=s.Applications)
def applications(f: F, db: Db, n: Annotated[int, Query(ge=1, le=100)] = 10) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        return q.applications(con, f, n)


@router.get("/workloads", response_model=s.Workloads)
def workloads(f: F, db: Db) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        return q.workload_families(con, f)


@router.get("/pricing", response_model=s.Pricing)
def pricing(f: F, db: Db, n: Annotated[int, Query(ge=1, le=100)] = 15) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        return q.pricing(con, f, n)


@router.get("/savings-headroom", response_model=s.Headroom)
def savings_headroom(f: F, db: Db, n: Annotated[int, Query(ge=1, le=100)] = 15) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        return q.savings_headroom(con, f, n)


@router.get("/cost/movement", response_model=s.Movement)
def cost_movement(f: F, db: Db, month_from: date, month_to: date,
                  n: Annotated[int, Query(ge=1, le=100)] = 15) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        return q.movement(con, f, month_from, month_to, n)


@router.get("/cost/breakdown", response_model=s.ServiceBreakdown)
def cost_breakdown(f: F, db: Db, service: Annotated[str, Query(min_length=1, max_length=MAX_VALUE_LEN)],
                   n: Annotated[int, Query(ge=1, le=200)] = 25) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        return q.service_breakdown(con, f, service, n)


@router.get("/dimensions/{dimension}", response_model=s.DimensionValues)
def dimension_values(dimension: str, f: F, db: Db, search: Annotated[str, Query(max_length=100)] = "",
                     limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        r = q.dimension_values(con, f, dimension, search, limit)
    labels = r.pop("labels")
    r["values"] = [{**v, "label": labels.get(v["value"])} for v in r["values"]]
    return r


@router.get("/commitments", response_model=s.CommitmentsDetail)
def commitments(f: F, db: Db, limit: Annotated[int, Query(ge=1, le=200)] = 100) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        return q.commitments_detail(con, f, limit)


@router.get("/commitments/savings-plan-hourly", response_model=s.SpHourly)
def savings_plan_hourly(f: F, db: Db) -> dict[str, Any]:
    """Savings-plan-eligible usage per hour not covered by a plan: latest day, 7 and 30 days, and the lowest day."""
    with db.cursor(f=f) as con:
        return q.sp_hourly(con, f)


@router.get("/reservations", response_model=s.Reservations)
def reservations(f: F, db: Db, n: Annotated[int, Query(ge=1, le=200)] = 15) -> dict[str, Any]:
    with db.cursor(f=f) as con:
        return q.reservations(con, f, n)


SQL_TIMEOUT_S = 20
SQL_HEADER = "x-azfinops"


@router.get("/data/imports", response_model=s.Imports)
def data_imports(db: Db, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    with db.cursor(require_current=False) as con:
        return dm.imports(con, limit)


@router.get("/data/quality", response_model=s.DataQuality)
def data_quality(db: Db, full: bool = False) -> dict[str, Any]:
    """Fast integrity checks by default; raw fact reconciliation runs only on explicit request."""
    with db.cursor(require_current=False) as con:
        return dm.quality_checks(con, full=full)


@router.get("/sql/schema", response_model=s.SchemaListing)
def sql_schema(db: Db) -> dict[str, Any]:
    with db.cursor(require_current=False) as con:
        return dm.schema(con)


@router.post("/sql/run", response_model=s.SqlResult)
def sql_run(body: s.SqlRequest, db: Db, x_azfinops: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    # A custom header cannot be sent cross-site without a CORS preflight, which this server never allows:
    # a web page on another origin cannot make the browser run SQL here.
    if x_azfinops != "1":
        raise HTTPException(status_code=403, detail="missing X-AzFinOps header")
    if not db.sql_enabled:
        return JSONResponse({"error": "The SQL workbench is disabled: the database could not be locked down."},
                            status_code=503)  # type: ignore[return-value]
    t0 = time.perf_counter()
    with db.cursor(timeout_s=SQL_TIMEOUT_S) as con:
        res = sqlbench.run(con, body.sql, body.limit)
    return {"columns": res.columns, "types": res.types, "rows": res.rows, "truncated": res.truncated,
            "limit": res.limit, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}


def _require_header(x_azfinops: str | None) -> None:
    if x_azfinops != "1":
        raise HTTPException(status_code=403, detail="missing X-AzFinOps header")


@router.get("/fx/rates", response_model=s.FxRates)
def fx_rates(request: Request) -> dict[str, Any]:
    return {"rates": fx.load(request.app.state.settings.data_dir)}


@router.get("/fx/combined")
def fx_combined(request: Request, db: Db, cost_kind: Annotated[str, Query(pattern="^(actual|amortized)$")],
                currency: Annotated[str, Query(pattern=r"^[A-Za-z]{3}$")] = "GBP") -> dict[str, Any]:
    """Which months and currencies are converted, at what rate, and which are missing a rate."""
    from .. import combined
    with db.cursor() as con:
        return combined.summary(con, cost_kind, currency.upper(), fx.load(request.app.state.settings.data_dir),
                                q.Filters(cost_kind, currency.upper(), combined=True))


@router.post("/fx/query", response_model=s.FxQueryResult, response_model_by_alias=True)
def fx_query(body: s.FxQuery, x_azfinops: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Looks up today's list-price rate on the Azure price list. Nothing is stored."""
    _require_header(x_azfinops)
    try:
        return fx.implied_rate(body.from_currency, body.to_currency, fx.http_get)
    except fx.FxUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/fx/rates", response_model=s.FxRate, response_model_by_alias=True)
def fx_save(body: s.FxSave, request: Request, x_azfinops: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    _require_header(x_azfinops)
    return fx.save(request.app.state.settings.data_dir, body.month, body.from_currency, body.to_currency, body.rate,
                   body.method, body.source, body.matched)


@router.get("/import/files", response_model=s.ImportFiles)
def import_list(request: Request) -> dict[str, Any]:
    """CSV files in the allowed import folders, with whether a file of that name was imported already."""
    db, settings = request.app.state.db, request.app.state.settings
    names: set[str] | None = None
    if not db.paused:
        with db.cursor(require_current=False) as con:
            names = {r[0] for r in con.execute(
                "SELECT f.file_name FROM import_files f JOIN import_batches b USING (batch_id) WHERE b.status = 'committed' "
                "UNION SELECT file_name FROM price_sheet_files").fetchall()}
    return jobs.list_files(settings, names)


@router.get("/import/status", response_model=s.ImportStatus)
def import_status(request: Request) -> dict[str, Any]:
    """Progress of the dashboard import; answers while the import runs, as it does not touch the database."""
    runner: jobs.ImportJobs = request.app.state.jobs
    return {**runner.snapshot(), "recovery_required": _get_db(request).recovery_required}


@router.post("/import/start", response_model=s.ImportStatus)
def import_start(body: s.ImportStart, request: Request, x_azfinops: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    _require_header(x_azfinops)
    settings = request.app.state.settings
    paths = [jobs.resolve(settings, i) for i in body.files]       # ValueError -> 422 for anything not listed
    try:
        runner: jobs.ImportJobs = request.app.state.jobs
        return runner.start(_get_db(request), settings, body.kind, paths, cost_kind=body.cost_kind,
                            supersede=body.supersede, reimport=body.reimport)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


def _web_dir(explicit: Path | None) -> Path | None:
    cand = explicit or (Path(os.environ["AZFINOPS_WEB_DIR"]) if os.environ.get("AZFINOPS_WEB_DIR")
                        else Path(__file__).resolve().parents[3] / "web" / "dist")
    return cand if cand.is_dir() else None


def create_app(settings: Settings | None = None, db_path: Path | None = None,
               allowed_hosts: tuple[str, ...] = LOCAL_HOSTS, web_dir: Path | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        app.state.db = Database(db_path or cfg.db_path, cfg)
        app.state.settings = cfg
        app.state.jobs = jobs.ImportJobs()
        try:
            yield
        finally:
            app.state.db.close()

    app = FastAPI(title="Azure FinOps (local)", version=VERSION, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.middleware("http")
    async def timing_and_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        sink: list[tuple[str, float]] = []
        token = request_timings.set(sink)
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        finally:
            request_timings.reset(token)
        total = (time.perf_counter() - t0) * 1000
        db_ms = sum(ms for name, ms in sink if name == "db")
        resp.headers["Server-Timing"] = f"total;dur={total:.1f}, db;dur={db_ms:.1f}"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api"):
            resp.headers["Cache-Control"] = "no-store"
        else:
            resp.headers["Content-Security-Policy"] = _CSP
        if total > SLOW_REQUEST_MS and request.url.path.startswith("/api"):
            log.warning("slow request %s %s total=%.0fms db=%.0fms", request.method, request.url.path, total, db_ms)
        return resp

    @app.exception_handler(ValueError)
    async def bad_request(_: Request, e: ValueError) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=422)

    @app.exception_handler(QueryTimeout)
    async def timeout(_: Request, e: QueryTimeout) -> JSONResponse:
        return JSONResponse({"error": str(e), "hint": "narrow the date range or add filters"}, status_code=504)

    @app.exception_handler(Paused)
    async def paused(_: Request, e: Paused) -> JSONResponse:
        return JSONResponse({"error": str(e), "importing": True}, status_code=503, headers={"Retry-After": "10"})

    @app.exception_handler(Overloaded)
    async def overloaded(_: Request, e: Overloaded) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=503, headers={"Retry-After": "2"})

    @app.exception_handler(StaleAggregates)
    async def stale_aggregates(_: Request, e: StaleAggregates) -> JSONResponse:
        return JSONResponse({"error": str(e), "recovery_required": True}, status_code=503)

    @app.exception_handler(duckdb.Error)
    async def db_error(request: Request, e: duckdb.Error) -> JSONResponse:
        ref = uuid.uuid4().hex[:8]
        log.error("database error ref=%s path=%s: %s", ref, request.url.path, e)  # detail stays in the log
        return JSONResponse({"error": "query failed", "ref": ref}, status_code=500)

    app.include_router(router)
    web = _web_dir(web_dir)
    if web is not None:
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
