"""Data Management queries: import history, data-quality checks and the schema listing.

Everything here is read-only and runs on a request cursor. Quality checks never change data; each one
reports pass, warn, fail or info with a plain sentence and the numbers behind it.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

TOLERANCE = Decimal("0.000001")


def _tables(con: Any) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()}


def imports(con: Any, limit: int = 50) -> dict[str, Any]:
    """Newest batches first, with their files and a summary of validation messages."""
    batches = con.execute(
        "SELECT batch_id, source_kind, cost_kind, is_synthetic, status, period_start, period_end, row_count, "
        "rejected_row_count, created_at, finished_at, notes FROM import_batches ORDER BY batch_id DESC LIMIT ?",
        [limit]).fetchall()
    ids = [b[0] for b in batches]
    files: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    val: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    if ids:
        ph = ",".join("?" * len(ids))
        for r in con.execute(
                f"SELECT batch_id, file_name, sha256, byte_count, row_count, schema_id FROM import_files "
                f"WHERE batch_id IN ({ph}) ORDER BY batch_id, partition_ordinal NULLS LAST, file_name", ids).fetchall():
            files[r[0]].append({"file_name": r[1], "sha256": (r[2] or "")[:12], "bytes": r[3], "rows": r[4],
                                "schema_id": r[5]})
        for r in con.execute(
                f"SELECT batch_id, severity, code, count(*), min(message), min(sample_value) FROM validation_results "
                f"WHERE batch_id IN ({ph}) GROUP BY 1, 2, 3 "
                f"ORDER BY batch_id, CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, 4 DESC",
                ids).fetchall():
            val[r[0]].append({"severity": r[1], "code": r[2], "count": int(r[3]), "message": r[4], "sample": r[5]})
    out = [{"batch_id": b[0], "source_kind": b[1], "cost_kind": b[2], "synthetic": bool(b[3]), "status": b[4],
            "period_start": b[5], "period_end": b[6], "rows": b[7], "rejected": b[8], "created_at": b[9],
            "finished_at": b[10], "notes": b[11], "files": files[b[0]], "validation": val[b[0]]} for b in batches]
    price: list[dict[str, Any]] = []
    if "price_sheet_files" in _tables(con):
        price = [{"file_name": r[0], "imported_at": r[1], "bytes": r[2], "rows_loaded": r[3], "rows_rejected": r[4]}
                 for r in con.execute("SELECT file_name, imported_at, bytes, rows_loaded, rows_rejected "
                                      "FROM price_sheet_files ORDER BY imported_at DESC, file_name").fetchall()]
    total = int(con.execute("SELECT count(*) FROM import_batches").fetchall()[0][0])
    return {"batches": out, "total_batches": total, "price_sheet_files": price}


def _check(id_: str, title: str, status: str, detail: str, **numbers: Any) -> dict[str, Any]:
    return {"id": id_, "title": title, "status": status, "detail": detail,
            "numbers": {k: (None if v is None else str(v)) for k, v in numbers.items()}}


def _reconciliation(con: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    diffs = con.execute("""
        WITH f AS (SELECT usage_month, cost_kind, billing_currency, sum(cost) AS cost, count(*) AS n
                   FROM v_cost_facts GROUP BY 1, 2, 3),
             a AS (SELECT usage_month, cost_kind, billing_currency, sum(cost) AS cost, sum(row_count) AS n
                   FROM agg_daily GROUP BY 1, 2, 3)
        SELECT coalesce(f.usage_month, a.usage_month), coalesce(f.cost_kind, a.cost_kind),
               coalesce(f.billing_currency, a.billing_currency),
               coalesce(f.cost, 0), coalesce(a.cost, 0), coalesce(f.n, 0), coalesce(a.n, 0)
        FROM f FULL JOIN a ON f.usage_month = a.usage_month AND f.cost_kind = a.cost_kind
                          AND f.billing_currency = a.billing_currency ORDER BY 1, 2, 3""").fetchall()
    wrong = [d for d in diffs if abs(Decimal(d[3]) - Decimal(d[4])) > TOLERANCE or int(d[5]) != int(d[6])]
    if not diffs:
        checks.append(_check("aggregates_match", "Aggregates match the imported rows", "info", "There is no data yet."))
    elif wrong:
        checks.append(_check("aggregates_match", "Aggregates match the imported rows", "fail",
                             "The daily aggregate does not equal the imported rows for: "
                             + "; ".join(f"{d[0]:%Y-%m} {d[1]} {d[2]} (rows {int(d[5]):,} vs {int(d[6]):,}, "
                                         f"cost difference {Decimal(d[3]) - Decimal(d[4]):f})" for d in wrong[:6])
                             + ". Run azfinops-aggregate to rebuild.", months_off=len(wrong)))
    else:
        checks.append(_check("aggregates_match", "Aggregates match the imported rows", "pass",
                             f"Cost and row counts agree exactly for all {len(diffs)} month/basis/currency combinations.",
                             combinations=len(diffs)))

    # 4. resource-month aggregate reproduces the daily aggregate
    rm = con.execute("""
        WITH d AS (SELECT usage_month, cost_kind, billing_currency, sum(cost) AS cost FROM agg_daily GROUP BY 1, 2, 3),
             r AS (SELECT usage_month, cost_kind, billing_currency, sum(cost) AS cost FROM agg_resource_month GROUP BY 1, 2, 3)
        SELECT coalesce(d.usage_month, r.usage_month), coalesce(d.cost_kind, r.cost_kind),
               coalesce(d.cost, 0), coalesce(r.cost, 0)
        FROM d FULL JOIN r ON d.usage_month = r.usage_month AND d.cost_kind = r.cost_kind
                          AND d.billing_currency = r.billing_currency ORDER BY 1, 2""").fetchall()
    rm_wrong = [x for x in rm if abs(Decimal(x[2]) - Decimal(x[3])) > TOLERANCE]
    if rm:
        checks.append(_check("resource_aggregate", "Resource-level aggregate matches the daily aggregate",
                             "pass" if not rm_wrong else "warn",
                             "Both aggregates give the same total for every month." if not rm_wrong else
                             "These months differ, so reservation, movement and savings screens may be stale: "
                             + ", ".join(f"{x[0]:%Y-%m} {x[1]}" for x in rm_wrong[:6]) + ". Run azfinops-aggregate.",
                             months_off=len(rm_wrong)))
    return checks


def quality_checks(con: Any, *, full: bool = False) -> dict[str, Any]:
    tables = _tables(con)
    checks: list[dict[str, Any]] = []
    pending = int(con.execute("SELECT count(*) FROM aggregate_invalidations").fetchone()[0])
    checks.append(_check("aggregates_current", "Committed imports are fully aggregated",
                         "fail" if pending else "pass",
                         "Committed data needs aggregate recovery. Cost screens are blocked. Stop the server, "
                         "run azfinops-aggregate, then restart it." if pending else
                         "No committed imports are waiting for an aggregate rebuild.", pending=pending))
    bad = con.execute("SELECT status, count(*) FROM import_batches WHERE status IN ('running','failed') "
                      "GROUP BY 1 ORDER BY 1").fetchall()
    if bad:
        checks.append(_check("batches", "Imports finished cleanly", "warn",
                             "Some import batches did not commit. Their rows are not used anywhere. "
                             + ", ".join(f"{n} {s}" for s, n in bad) + ".", **{s: n for s, n in bad}))
    else:
        n = int(con.execute("SELECT count(*) FROM import_batches WHERE status = 'committed'").fetchall()[0][0])
        checks.append(_check("batches", "Imports finished cleanly", "pass" if n else "warn",
                             f"{n} committed batch(es), none failed or still running." if n else "No data has been imported.",
                             committed=n))
    rej = con.execute("SELECT coalesce(sum(rejected_row_count), 0), coalesce(sum(row_count), 0) "
                      "FROM import_batches WHERE status = 'committed'").fetchall()[0]
    rejected, loaded = int(rej[0]), int(rej[1])
    checks.append(_check("rejected", "Rows rejected on import", "pass" if rejected == 0 else "warn",
                         "No rows were rejected." if rejected == 0 else
                         f"{rejected:,} row(s) were rejected on import (against {loaded:,} loaded). "
                         "Their reasons are in the import history.", rejected=rejected, loaded=loaded))
    if full:
        checks.extend(_reconciliation(con))
    else:
        for id_, title in (("aggregates_match", "Aggregates match the imported rows"),
                           ("resource_aggregate", "Resource-level aggregate matches the daily aggregate")):
            checks.append(_check(id_, title, "info",
                                 "Full reconciliation has not been run by this request. Use Run full reconciliation "
                                 "to compare costs and row counts; that scans the imported data."))
    stats = con.execute(
        "SELECT bool_or(is_synthetic), bool_and(is_synthetic), "
        "coalesce(sum(row_count) FILTER (WHERE charge_type = 'Usage'), 0), "
        "coalesce(sum(payg_missing_rows), 0), "
        "coalesce(sum(cost), 0), coalesce(sum(cost) FILTER "
        "(WHERE application IN ('(unattributed)', '(untagged)')), 0), "
        "coalesce(sum(row_count), 0) FROM agg_daily").fetchall()[0]

    # 5. month completeness
    inc = con.execute(
        "SELECT usage_month, cost_kind, billing_account_id, billing_currency FROM aggregate_scope_freshness "
        "WHERE NOT period_complete ORDER BY 1, 2, 3, 4").fetchall()
    if stats[6]:
        checks.append(_check("complete", "Every month is fully covered", "pass" if not inc else "warn",
                             "Every month in the data has all of its days." if not inc else
                             "Imported data does not cover the whole month for: "
                             + ", ".join(f"{m:%Y-%m} ({k}, Enrollment {a}, {c})" for m, k, a, c in inc)
                             + ". Totals for these scopes are partial.",
                             incomplete=len(inc)))

    # 6. mixed synthetic and real data
    syn = stats[:2]
    if syn[0] is not None:
        if syn[0] and not syn[1]:
            checks.append(_check("synthetic", "Real and synthetic data are not mixed", "fail",
                                 "Synthetic (generated) rows are present alongside real rows. Totals mix test data "
                                 "with actual cost."))
        elif syn[0]:
            checks.append(_check("synthetic", "Real and synthetic data are not mixed", "info",
                                 "All data in this database is synthetic (generated)."))
        else:
            checks.append(_check("synthetic", "Real and synthetic data are not mixed", "pass",
                                 "All data is imported, none is generated."))

    # 7. pay-as-you-go prices present on usage rows
    pg = stats[2:4]
    if pg[0]:
        share = int(pg[1]) / int(pg[0])
        checks.append(_check("payg", "Usage rows carry a pay-as-you-go price", "pass" if share == 0 else "info",
                             "The daily aggregate reports a baseline price on every usage row." if share == 0 else
                             f"{int(pg[1]):,} of {int(pg[0]):,} usage rows ({share:.1%}) have no pay-as-you-go price. "
                             "They are left out of coverage and price comparisons, never treated as zero.",
                             missing=int(pg[1]), usage_rows=int(pg[0])))

    # 8. attribution
    at = stats[4:6]
    if Decimal(at[0]) != 0:
        gap = Decimal(at[1]) / Decimal(at[0])
        checks.append(_check("attribution", "Cost attributed to an application", "info",
                             f"{1 - gap:.1%} of cost has an application tag; {gap:.1%} does not. "
                             "See the Applications workspace for where the gap is.", unattributed_share=f"{gap:.4f}"))

    # 9. price sheet coverage of usage months
    months = [r[0] for r in con.execute(
        "SELECT DISTINCT usage_month FROM aggregate_scope_freshness ORDER BY 1").fetchall()]
    if months and "price_sheet" in tables:
        have = {r[0] for r in con.execute(
            "SELECT m.usage_month FROM (SELECT DISTINCT usage_month FROM aggregate_scope_freshness) m WHERE EXISTS "
            "(SELECT 1 FROM price_sheet p WHERE p.effective_start <= m.usage_month AND p.effective_end >= m.usage_month)"
        ).fetchall()}
        missing = [m for m in months if m not in have]
        any_sheet = int(con.execute("SELECT count(*) FROM price_sheet").fetchall()[0][0]) > 0
        if not any_sheet:
            checks.append(_check("price_sheet", "Price sheet covers each usage month", "info",
                                 "No price sheet is loaded, so the savings-plan headroom is unavailable. "
                                 "Run azfinops-pricesheet."))
        else:
            checks.append(_check("price_sheet", "Price sheet covers each usage month", "pass" if not missing else "warn",
                                 "Every month with usage has a price sheet." if not missing else
                                 "No price sheet for: " + ", ".join(f"{m:%Y-%m}" for m in missing)
                                 + ". Cost in those months gets no savings estimate.", months_missing=len(missing)))

    # 10. subscription names
    if "dim_subscription" in tables:
        s = con.execute("SELECT count(*), count(n.subscription_id) FROM (SELECT DISTINCT subscription_id FROM agg_daily "
                        "WHERE subscription_id IS NOT NULL) a LEFT JOIN dim_subscription n "
                        "ON n.subscription_id = lower(a.subscription_id)").fetchall()[0]
        if int(s[0]):
            checks.append(_check("names", "Subscriptions have display names", "pass" if s[0] == s[1] else "info",
                                 f"{int(s[1])} of {int(s[0])} subscriptions have a name." +
                                 ("" if s[0] == s[1] else " The rest show their id. Run azfinops-names to load names."),
                                 named=int(s[1]), subscriptions=int(s[0])))

    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3}
    summary = {k: sum(1 for c in checks if c["status"] == k) for k in order}
    return {"checks": checks, "summary": summary, "full_reconciliation": full}


def schema(con: Any) -> dict[str, Any]:
    """Tables and views with their columns, for the workbench schema browser."""
    cols = con.execute(
        "SELECT c.table_name, t.table_type, c.column_name, c.data_type FROM information_schema.columns c "
        "JOIN information_schema.tables t USING (table_schema, table_name) "
        "WHERE c.table_schema = 'main' ORDER BY c.table_name, c.ordinal_position").fetchall()
    out: dict[str, dict[str, Any]] = {}
    for table, kind, col, typ in cols:
        out.setdefault(table, {"name": table, "kind": "view" if kind == "VIEW" else "table", "columns": []})
        out[table]["columns"].append({"name": col, "type": typ})
    return {"tables": list(out.values())}
