"""Month-partitioned aggregate rebuilds from committed facts (v_cost_facts).

Only the affected usage_months are rebuilt, each in its own transaction, so a rebuild never leaves a
month half-written. Aggregates are derived data and can be dropped and rebuilt at any time.

Precision: costs and quantities are summed as exact decimals. payg_equivalent (price x quantity) is
computed in double precision, because the product of two DECIMAL(38,18) values overflows DuckDB's
decimal type above 100 (found on real data). It is a price-list estimate, not a billed amount.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

import duckdb

from .config import Settings

UNATTRIBUTED = "(unattributed)"   # has tags, but none of the configured keys carries a value
UNTAGGED = "(untagged)"           # the row has no tags at all (e.g. shared network such as ExpressRoute)
NO_ENVIRONMENT = "(none)"


def _tag_expr(keys: tuple[str, ...], fallback: str, *, lowercase: bool = False,
              untagged: str | None = None) -> str:
    """First non-blank value among `keys`, in order. `lowercase` folds case so the same ID written
    'APP-01137' and 'app-01137' is one application. Key lookup itself stays case-sensitive."""
    def val(k: str) -> str:
        v = f"NULLIF(trim(json_extract_string(tags, '$.\"{k}\"')), '')"
        return f"lower({v})" if lowercase else v
    last = f"'{fallback}'"
    if untagged:
        last = (f"CASE WHEN tags IS NULL OR trim(tags) IN ('', '{{}}', 'null') "
                f"THEN '{untagged}' ELSE '{fallback}' END")
    return f"COALESCE({', '.join(val(k) for k in keys)}, {last})"


def affected_months(con: duckdb.DuckDBPyConnection, batch_ids: Iterable[int]) -> list[date]:
    ids = list(batch_ids)
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT usage_month FROM cost_facts WHERE batch_id IN ({ph}) ORDER BY 1", ids).fetchall()]


def rebuild(db_path: Path, settings: Settings | None = None, months: Iterable[date] | None = None) -> list[date]:
    """Rebuild aggregates for `months` (default: every month present, and clear stale ones)."""
    s = settings or Settings()
    con = duckdb.connect(str(db_path))
    try:
        s.apply(con)
        have = {r[0] for r in con.execute("SELECT DISTINCT usage_month FROM v_cost_facts").fetchall()}
        stale: set[date] = set()
        if months is None:
            built = {r[0] for r in con.execute(
                "SELECT usage_month FROM agg_daily UNION SELECT usage_month FROM aggregate_invalidations").fetchall()}
            stale = built - have
            todo = sorted(have)
        else:
            requested = set(months)
            todo = sorted(requested & have)
            stale = requested - have
        app = _tag_expr(s.app_tag_keys, UNATTRIBUTED, lowercase=True, untagged=UNTAGGED)
        env = _tag_expr(s.env_tag_keys, NO_ENVIRONMENT)
        for m in stale:
            _clear(con, m)
        for m in todo:
            _build_month(con, m, app, env)
        return todo
    finally:
        con.close()


def _clear(con: duckdb.DuckDBPyConnection, m: date) -> None:
    con.execute("BEGIN")
    try:
        for t in ("agg_daily", "agg_resource_month"):
            con.execute(f"DELETE FROM {t} WHERE usage_month = ?", [m])
        con.execute("DELETE FROM aggregate_freshness WHERE usage_month = ?", [m])
        con.execute("DELETE FROM aggregate_scope_freshness WHERE usage_month = ?", [m])
        con.execute("DELETE FROM aggregate_invalidations WHERE usage_month = ?", [m])
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _build_scope_freshness(con: duckdb.DuckDBPyConnection, m: date) -> None:
    con.execute("DELETE FROM aggregate_scope_freshness WHERE usage_month = ?", [m])
    con.execute("""
        INSERT INTO aggregate_scope_freshness
        SELECT usage_month, cost_kind, billing_account_id, billing_currency,
               min(usage_date), max(usage_date), CAST(count(DISTINCT usage_date) AS INTEGER),
               sum(row_count),
               count(DISTINCT usage_date) = date_diff(
                 'day', usage_month, CAST(usage_month + INTERVAL 1 MONTH AS DATE)),
               CAST(now() AS TIMESTAMP)
        FROM agg_daily WHERE usage_month = ?
        GROUP BY usage_month, cost_kind, billing_account_id, billing_currency""", [m])


def _build_month(con: duckdb.DuckDBPyConnection, m: date, app: str, env: str) -> None:
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM agg_daily WHERE usage_month = ?", [m])
        con.execute("DELETE FROM agg_resource_month WHERE usage_month = ?", [m])
        con.execute(f"""
            INSERT INTO agg_daily (usage_date, usage_month, cost_kind, billing_account_id, billing_currency,
              subscription_id, resource_group, region, meter_category, meter_subcategory, charge_type, pricing_model,
              publisher_type, application, environment, cost, payg_equivalent, cost_with_payg,
              payg_missing_rows, contract_basis_rows, quantity_hours, row_count, is_synthetic)
            SELECT usage_date, usage_month, cost_kind, billing_account_id, billing_currency,
              subscription_id, resource_group, region, meter_category, meter_subcategory, charge_type, pricing_model,
              publisher_type, {app} AS application, {env} AS environment,
              sum(cost),
              CAST(sum(CAST(payg_price AS DOUBLE) * CAST(quantity AS DOUBLE))
                   FILTER (WHERE payg_price IS NOT NULL AND quantity IS NOT NULL) AS DECIMAL(38,18)),
              sum(cost) FILTER (WHERE payg_price IS NOT NULL),
              count(*) FILTER (WHERE payg_price IS NULL AND charge_type = 'Usage'),
              count(*) FILTER (WHERE payg_basis = 'contract'),
              sum(quantity) FILTER (WHERE lower(trim(unit)) = '1 hour'),
              count(*), bool_or(is_synthetic)
            FROM v_cost_facts WHERE usage_month = ?
            GROUP BY ALL""", [m])
        con.execute(f"""
            INSERT INTO agg_resource_month (usage_month, cost_kind, billing_account_id, billing_currency, subscription_id,
              resource_group, resource_id, resource_name, meter_category, meter_subcategory, meter_region, meter_id,
              meter_name, unit, charge_type, pricing_model, commitment_id, reservation_name, offer_id, application, environment, cost, quantity,
              row_count, is_synthetic, first_date, last_date, active_days)
            SELECT usage_month, cost_kind, billing_account_id, billing_currency, subscription_id, resource_group, resource_id,
              resource_name, meter_category, meter_subcategory, meter_region, meter_id, meter_name, unit, charge_type,
              pricing_model,
              COALESCE(reservation_id, benefit_id) AS commitment_id, max(reservation_name), offer_id, {app} AS application,
              {env} AS environment, sum(cost), sum(quantity), count(*), bool_or(is_synthetic),
              min(usage_date), max(usage_date), CAST(count(DISTINCT usage_date) AS INTEGER)
            FROM v_cost_facts WHERE usage_month = ?
            GROUP BY ALL""", [m])
        _build_scope_freshness(con, m)
        stats = con.execute(
            "SELECT cost_kind, sum(source_row_count), bool_and(period_complete) "
            "FROM aggregate_scope_freshness WHERE usage_month = ? GROUP BY cost_kind", [m]).fetchall()
        batches = ",".join(str(r[0]) for r in con.execute(
            "SELECT DISTINCT batch_id FROM v_cost_facts WHERE usage_month = ? ORDER BY 1", [m]).fetchall())
        for kind, n, complete in stats:  # INSERT OR REPLACE: no delete+insert of one key in a transaction
            for name in ("agg_daily", "agg_resource_month"):
                con.execute("INSERT OR REPLACE INTO aggregate_freshness VALUES "
                            "(?, ?, ?, CAST(now() AS TIMESTAMP), ?, ?, ?)",
                            [name, m, kind, n, batches, bool(complete)])
        kinds = [r[0] for r in stats]
        con.execute("DELETE FROM aggregate_freshness WHERE usage_month = ? AND cost_kind NOT IN ("
                    + ",".join("?" * len(kinds)) + ")", [m, *kinds])
        con.execute("DELETE FROM aggregate_invalidations WHERE usage_month = ?", [m])
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def main() -> None:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description="Rebuild aggregates (all months, or the given YYYY-MM)")
    ap.add_argument("--db", type=Path)
    ap.add_argument("--month", action="append", help="YYYY-MM; repeatable")
    a = ap.parse_args()
    s = Settings.from_env()
    months = [date.fromisoformat(m + "-01") for m in a.month] if a.month else None
    from .migrations.runner import migrate
    db = a.db or s.db_path
    applied = migrate(db)   # a backup copy is written first if migrations are pending on an existing database
    if applied:
        print(f"applied migrations: {applied}")
    done = rebuild(db, s, months)
    print("rebuilt:", ", ".join(m.isoformat()[:7] for m in done) or "nothing")


if __name__ == "__main__":
    main()
