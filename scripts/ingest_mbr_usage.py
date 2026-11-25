"""Bounded raw-usage grouping for the standalone MBR report, never the main database.

The twelve Power Query keys are retained, plus BillingCurrency and ChargeType.
Quantities and signed costs use exact decimals. Publication is explicit and atomic.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import build_mbr_dashboard as mbr
import duckdb
import mbr_uploads as uploads

from azfinops.combined import rates_to, resolve
from azfinops.fx import decimal_rate
from azfinops.ingestion.mapping import q

GROUP_KEYS = (
    "BillingPeriodStartDate", "BillingAccountId", "MeterCategory", "Product",
    "SubscriptionName", "IsAzureCreditEligible", "ProductOrderName", "PublisherType",
    "PricingModel", "ResourceLocation", "EffectivePrice", "UnitPrice",
)
EXTRA_KEYS = ("BillingCurrency", "ChargeType")
REQUIRED = (*GROUP_KEYS, *EXTRA_KEYS, "Date", "Quantity", "Cost")
MONEY_TYPE = "DECIMAL(38,18)"
LOG = logging.getLogger("mbr-usage")


def cleanup_spill(temporary: tempfile.TemporaryDirectory[str]) -> None:
    for attempt in range(5):
        try:
            temporary.cleanup()
            return
        except PermissionError:
            if attempt == 4:
                raise
            LOG.warning("Windows locked the owned spill directory; retrying cleanup (%s/5)", attempt + 1)
            time.sleep(0.2 * (attempt + 1))


@contextmanager
def spill_directory(parent: Path) -> Iterator[str]:
    temporary = tempfile.TemporaryDirectory(prefix=".usage-group-", dir=parent)
    try:
        yield temporary.name
    finally:
        cleanup_spill(temporary)


def install_decimal_normalizer(con: Any) -> None:
    # DuckDB's text-to-decimal cast rounds exact ties away from zero. Correct only ties;
    # casting through a shorter intermediate decimal would double-round long source values.
    raw = "trim(value)"
    mantissa = f"split_part(lower({raw}), 'e', 1)"
    digits = f"regexp_replace({mantissa}, '[+.\\-]', '', 'g')"
    exponent = f"coalesce(try_cast(nullif(split_part(lower({raw}), 'e', 2), '') AS INTEGER), 0)"
    discarded = f"(length(split_part({mantissa}, '.', 2)) - {exponent} - 18)"
    kept = f"CASE WHEN length({digits}) > {discarded} THEN substr({digits}, length({digits}) - {discarded}, 1) ELSE '0' END"
    tie = (
        f"{discarded} > 0 AND {discarded} <= length({digits}) "
        f"AND regexp_full_match(substr({digits}, length({digits}) - {discarded} + 1), '50*') "
        f"AND ({kept}) IN ('0','2','4','6','8')"
    )
    converted = f"try_cast({raw} AS {MONEY_TYPE})"
    expression = (
        f"CASE WHEN {tie} THEN {converted} - "
        f"CASE WHEN starts_with({raw}, '-') THEN -0.000000000000000001::{MONEY_TYPE} "
        f"ELSE 0.000000000000000001::{MONEY_TYPE} END ELSE {converted} END"
    )
    con.execute(f"CREATE OR REPLACE MACRO mbr_decimal(value) AS ({expression})")


def _checked_text(name: str, *, optional: bool = False) -> str:
    value = f"nullif(trim({q(name)}), '')"
    return value if optional else f"coalesce({value}, error('Missing {name} in usage file'))"


def grouping_sql() -> str:
    parsed = []
    for name in GROUP_KEYS:
        if name == "BillingPeriodStartDate":
            expression = f"CAST(try_strptime({_checked_text(name)}, ['%m/%d/%Y','%Y-%m-%d']) AS DATE)"
            expression = f"coalesce({expression}, error('Invalid BillingPeriodStartDate'))"
        elif name in ("EffectivePrice", "UnitPrice"):
            raw = _checked_text(name, optional=True)
            expression = f"CASE WHEN {raw} IS NULL THEN NULL ELSE coalesce(mbr_decimal({raw}), error('Invalid {name}')) END"
        elif name == "IsAzureCreditEligible":
            raw = f"lower({_checked_text(name, optional=True)})"
            expression = f"CASE WHEN {raw} IS NULL THEN NULL WHEN {raw} IN ('true','false') THEN {raw} ELSE error('Invalid IsAzureCreditEligible') END"
        else:
            expression = _checked_text(name, optional=name in ("Product", "ProductOrderName", "ResourceLocation"))
        parsed.append(f"{expression} AS {q(name)}")
    parsed.extend([
        f"upper({_checked_text('BillingCurrency')}) AS BillingCurrency",
        f"{_checked_text('ChargeType')} AS ChargeType",
        f"coalesce(mbr_decimal({_checked_text('Cost')}), error('Invalid Cost in usage file')) AS _cost",
        (f"CASE WHEN {_checked_text('Quantity', optional=True)} IS NULL THEN NULL "
         f"ELSE coalesce(mbr_decimal({_checked_text('Quantity', optional=True)}), error('Invalid Quantity')) END AS _quantity"),
        f"coalesce(CAST(try_strptime({_checked_text('Date')}, ['%m/%d/%Y','%Y-%m-%d']) AS DATE), error('Invalid usage Date')) AS _usage_date",
    ])
    keys = ", ".join(q(name) for name in (*GROUP_KEYS, *EXTRA_KEYS))
    scopes = "BillingPeriodStartDate, BillingAccountId, BillingCurrency"
    return (
        "CREATE OR REPLACE TEMP TABLE _mbr_groups AS WITH parsed AS (SELECT "
        + ", ".join(parsed)
        + " FROM _mbr_raw), checked AS (SELECT * REPLACE ("
        "CASE WHEN day(BillingPeriodStartDate) != 1 THEN error('Billing period is not a month start') "
        "WHEN date_trunc('month', _usage_date) != BillingPeriodStartDate THEN error('Usage day outside billing month') "
        "ELSE BillingPeriodStartDate END AS BillingPeriodStartDate, "
        "CASE WHEN NOT regexp_full_match(BillingCurrency, '[A-Z]{3}') THEN error('Invalid billing currency') "
        "ELSE BillingCurrency END AS BillingCurrency) FROM parsed) "
        f"SELECT {keys}, grouping(Product) AS _scope_total, "
        "sum(_cost) AS Cost, CASE WHEN count(_quantity) = count(*) THEN sum(_quantity) END AS Quantity, "
        "count(*) AS SourceRowCount, count(*) - count(_quantity) AS MissingQuantityRows, "
        "min(_usage_date) AS FirstUsageDate, max(_usage_date) AS LastUsageDate, count(DISTINCT _usage_date) AS UsageDays "
        f"FROM checked GROUP BY GROUPING SETS (({keys}), ({scopes}))"
    )


def _csv_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        headers = next(csv.reader(handle), [])
    if len(headers) != len(set(headers)):
        raise ValueError(f"{path.name}: duplicate source headers")
    missing = sorted(set(REQUIRED) - set(headers))
    if missing:
        raise ValueError(f"{path.name}: missing raw usage columns: {', '.join(missing)}")
    return headers


def _cursor_rows(cursor: Any) -> Iterator[dict[str, Any]]:
    names = [column[0] for column in cursor.description]
    while batch := cursor.fetchmany(5000):
        for values in batch:
            yield dict(zip(names, values))


def group_file(con: Any, path: Path) -> list[dict[str, Any]]:
    header = _csv_header(path)
    con.read_csv(
        str(path), header=True, columns={name: "VARCHAR" for name in header},
        auto_detect=False, delimiter=",", quotechar='"', escapechar='"', encoding="utf-8",
        null_padding=False, ignore_errors=False, strict_mode=True,
    ).create_view("_mbr_raw", replace=True)
    con.execute(grouping_sql())
    scopes = list(_cursor_rows(con.execute("SELECT * FROM _mbr_groups WHERE _scope_total = 1")))
    if not scopes:
        raise ValueError(f"{path.name}: no populated usage records")
    fine = con.execute(
        "SELECT BillingPeriodStartDate, BillingAccountId, BillingCurrency, sum(Cost), sum(SourceRowCount) "
        "FROM _mbr_groups WHERE _scope_total = 0 GROUP BY 1,2,3"
    ).fetchall()
    actual = {(month, account, currency): (cost, count) for month, account, currency, cost, count in fine}
    for scope in scopes:
        key = scope["BillingPeriodStartDate"], scope["BillingAccountId"], scope["BillingCurrency"]
        if actual.get(key) != (scope["Cost"], scope["SourceRowCount"]):
            raise ValueError(f"{path.name}: grouped money/row counts do not reconcile to the independent source rollup")
    return scopes


def _base_inputs(current: Mapping[str, Any]) -> tuple[dict[str, str], list[list[Any]]]:
    names = {}
    for source in current["meta"].get("sources", [current["meta"]]):
        for name, accounts in source["billing_accounts"].items():
            for account in accounts:
                if account in names and names[account] != name:
                    raise ValueError("Conflicting enrollment display identities in the base report")
                names[account] = name
    discounts: dict[str, set[str | None]] = {}
    for row in current["meta"].get("discount_inputs", []):
        discounts.setdefault(row["product"], set()).add(row["discount"])
    table = [[None, None, None, None, "Product", "Discount"]]
    table.extend(
        [None, None, None, None, product, next(iter(values)) if len(values) == 1 else None]
        for product, values in sorted(discounts.items())
    )
    return names, table


def add_basis_warnings(snapshot: dict[str, Any]) -> None:
    sources = snapshot["meta"].get("sources", [snapshot["meta"]])
    charges: dict[int, dict[str, int]] = {}
    for index, row in enumerate(snapshot["rows"]):
        source_id = snapshot["row_sources"][index]
        value = snapshot["row_native_costs"][index]
        if value is None:
            continue
        charge = snapshot["row_charge_types"][index]
        totals = charges.setdefault(source_id, {})
        totals[charge] = totals.get(charge, 0) + int(value)
    for index, source in enumerate(sources):
        if "raw_usage" not in source:
            continue
        totals = charges.get(index, {})
        source["raw_usage"]["native_charge_totals"] = {
            charge: mbr.amount_string(value) for charge, value in sorted(totals.items())
        }
        suspect = {charge: value for charge, value in totals.items() if charge in ("Purchase", "Refund") and value}
        if snapshot["meta"]["basis"] == "amortized" and suspect:
            warning = {
                "code": "amortized-purchase", "title": "Verify amortized basis: non-zero purchase/refund charges",
                "source_name": source["source_name"],
                "detail": "; ".join(f"{charge} {mbr.amount_string(value)} {source['raw_usage']['native_currency']}" for charge, value in suspect.items())
                + ". Cost basis is the report declaration, not certified by the filename. Signed source costs are retained; nothing was silently excluded.",
            }
            if not any(row["code"] == warning["code"] and row.get("source_name") == warning["source_name"] for row in snapshot["audit"]["warnings"]):
                snapshot["audit"]["warnings"].append(warning)


def build_file_snapshot(
    con: Any, path: Path, scopes: list[dict[str, Any]], current: Mapping[str, Any],
    rates: list[dict[str, Any]], expected_months: set[str],
) -> dict[str, Any]:
    names, discounts = _base_inputs(current)
    scope_keys = [(row["BillingCurrency"], row["BillingPeriodStartDate"].isoformat()[:7]) for row in scopes]
    if any(month not in expected_months for _currency, month in scope_keys):
        raise ValueError(f"{path.name}: file contains billing months outside the requested reporting window")
    if any(month > date.today().strftime("%Y-%m") for _currency, month in scope_keys):
        raise ValueError("Future billing months cannot advance report retention")
    if len({(row["BillingAccountId"], row["BillingCurrency"], row["BillingPeriodStartDate"]) for row in scopes}) != 1:
        raise ValueError("Use one raw file per enrollment/native-currency/billing-month scope, with all partitions explicit")
    native = scopes[0]["BillingCurrency"]
    month = scopes[0]["BillingPeriodStartDate"].isoformat()[:7]
    chosen = resolve("GBP", rates, scope_keys)
    if native != "GBP" and (native, month) not in chosen:
        raise ValueError(f"No saved {native}-to-GBP rate exists; no amount was guessed or valued at zero")
    conversion = chosen.get((native, month), {"rate": Decimal(1), "rate_month": month, "exact": True})
    rate = conversion["rate"]
    provenance = next(
        (row for row in rates_to("GBP", rates) if row["from"] == native and row["month"] == conversion["rate_month"]),
        None,
    )
    account = scopes[0]["BillingAccountId"]
    name = names.get(account, f"Enrollment {account}")
    grouping_count = con.execute("SELECT count(*) FROM _mbr_groups WHERE _scope_total = 0").fetchone()[0]
    profile = {
        "source_name": path.name, "source_sha256": uploads.file_digest(path),
        "source_modified_utc": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
        "extracted_utc": datetime.now(UTC).isoformat(), "data_rows": grouping_count,
        "conversion_rates": {name: format(rate, "f")},
        "sheets": {
            "Usage declaration": {
                "start_row": 1, "start_column": 1,
                "values": [[f"Based on {current['meta']['basis'].title()} Cost"], ["Currency: GBP"]],
            },
            "Command Panel": {"start_row": 1, "start_column": 1, "values": discounts},
        },
    }

    def rows() -> Iterator[dict[str, Any]]:
        cursor = con.execute("SELECT * FROM _mbr_groups WHERE _scope_total = 0")
        for group in _cursor_rows(cursor):
            model = mbr.MODEL_ALIASES.get(group["PricingModel"], group["PricingModel"])
            allocation = "Unused" if group["ChargeType"] in ("UnusedReservation", "UnusedSavingsPlan") else (
                "Used" if group["ChargeType"] == "Usage" and model in ("Reservation", "SavingsPlan") else mbr.MISSING
            )
            subscription = group["SubscriptionName"]
            with localcontext() as ctx:
                ctx.prec = 80
                gbp = mbr.amount_string(mbr.units(group["Cost"] * rate, "Grouped usage conversion"))
            yield {
                **group, "BillingPeriodStartDate": group["BillingPeriodStartDate"].isoformat(),
                "Enrolment": name, "Cost GBP": gbp, "Effective": group["EffectivePrice"],
                "Unit": group["UnitPrice"], "Usage Type": "Market Place" if group["PublisherType"] == "Marketplace" else "Azure",
                "Utilization Type": allocation, "Source.Name": path.name,
                "Sub Type": "Greenfield" if any(marker in subscription.casefold() for marker in ("a10", "a1b", "a0", "a1a")) else "Brownfield",
            }

    snapshot = mbr.build_snapshot(profile, rows(), source_row_counts=True)
    fx_detail = {
        "month": month, "from": native, "to": "GBP", "rate": format(rate, "f"),
        "rate_month": conversion["rate_month"], "estimated": not conversion["exact"],
        "source": provenance.get("source", "Saved inverse rate") if provenance else "Native GBP; no conversion",
        "method": provenance.get("method", "saved") if provenance else "native",
        "native_total": format(scopes[0]["Cost"], "f"),
        "definition": "Saved monthly conversion applied once per exact grouped cost; nearest-month rates are estimated, not invoice rates.",
    }
    usage_evidence = {
        "path": str(path), "bytes": path.stat().st_size,
        "raw_rows": scopes[0]["SourceRowCount"], "power_query_groups": grouping_count,
        "group_keys": list(GROUP_KEYS), "preserved_extra_keys": list(EXTRA_KEYS),
        "native_currency": native, "native_total": format(scopes[0]["Cost"], "f"),
        "first_usage_date": scopes[0]["FirstUsageDate"].isoformat(),
        "last_usage_date": scopes[0]["LastUsageDate"].isoformat(), "usage_days": scopes[0]["UsageDays"],
        "precision": "Source text normalized half-even to DECIMAL(38,18); no binary floating-point money",
        "reconciliation": "Independent raw-scope rollup equals detailed group sums and counts exactly",
    }
    label = (
        f"Currency: GBP | {native} {month} at {format(rate, 'f')} GBP/{native}; "
        f"{'ESTIMATED nearest saved month' if fx_detail['estimated'] else 'saved/native month'} {conversion['rate_month']}. "
        "Not an invoice rate."
    )
    for metadata in (snapshot["meta"], snapshot["meta"]["sources"][0]):
        metadata["currency_label"] = label
        metadata["credit_inputs"] = current["meta"].get("credit_inputs", [])
        metadata["fx_details"] = [fx_detail]
        metadata["raw_usage"] = usage_evidence
        metadata["count_definition"] = "Original usage records, not grouped worksheet rows"
    snapshot["audit"]["warnings"].append({
        "code": "usage-transform", "title": "Usage grouping and inherited assumptions",
        "detail": "Power Query's twelve keys plus currency/charge type; Quantity/Cost summed exactly. "
        "Portfolio naming markers and explicit tier discounts are inherited report assumptions, not independently validated application/price data.",
    })
    if fx_detail["estimated"]:
        snapshot["audit"]["warnings"].append({
            "code": "estimated-fx", "title": "Estimated FX: no own-month rate is saved",
            "detail": f"{month} {native} uses the saved {conversion['rate_month']} rate {format(rate, 'f')}; "
            f"{fx_detail['source']}. This is not an invoice conversion or a newly fetched rate.",
        })
    add_basis_warnings(snapshot)
    return snapshot


def run(
    files: list[Path], dashboard: Path, fx_file: Path, months: list[str] | None, accounts: list[str],
    *, apply: bool = False, allow_missing: bool = False, year: int | None = None, replace: bool = False,
) -> dict[str, Any]:
    if year is not None and (months is not None or not 2000 <= year <= date.today().year):
        raise ValueError("Choose one reporting year, not a future year or a year together with --months")
    if year is None and (months is None or len(set(months)) != 2 or any(
        not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) for month in months
    )):
        raise ValueError("Specify exactly two distinct YYYY-MM billing months")
    requested_months = months if months is not None else [f"{year}-{number:02d}" for number in range(1, 13)]
    if len(files) != len({path.resolve() for path in files}):
        raise ValueError("Duplicate input paths are not allowed")
    original_revision = uploads.file_digest(dashboard)
    current = mbr.read_dashboard(dashboard)
    history = current.get("annual_history", current)
    known = {
        source["source_sha256"]: source for source in history["meta"].get("sources", [history["meta"]])
        if source.get("available_scopes")
    }
    rates = json.loads(fx_file.read_text(encoding="utf-8"))
    if not isinstance(rates, list):
        raise TypeError("Saved FX input must be a list of dated rate records")
    for row in rates:
        decimal_rate(row["rate"])
    snapshots = []
    skipped = []
    with spill_directory(dashboard.parent) as directory:
        con = duckdb.connect()
        try:
            con.execute("SET memory_limit='2GB'")
            con.execute("SET threads=2")
            con.execute("SET preserve_insertion_order=false")
            con.execute("SET temp_directory = ?", [directory])
            install_decimal_normalizer(con)
            for path in files:
                before = path.stat()
                if year is not None:
                    digest = uploads.file_digest(path)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError(f"{path.name} changed during hashing; no file was skipped or published")
                    if digest in known:
                        source = known[digest]
                        if any(not scope["month"].startswith(f"{year}-") for scope in source["available_scopes"]):
                            raise ValueError(f"{path.name}: loaded source belongs to a different year")
                        if any(account not in accounts for values in source["billing_accounts"].values() for account in values):
                            raise ValueError(f"{path.name}: loaded source has an unexpected enrollment")
                        skipped.append({"source_name": path.name, "sha256": digest})
                        print(f"Unchanged; skipping grouping: {path.name}", flush=True)
                        continue
                print(f"Grouping {path.name} ({before.st_size:,} bytes)", flush=True)
                scopes = group_file(con, path)
                snapshot = build_file_snapshot(con, path, scopes, current, rates, set(requested_months))
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError(f"{path.name} changed during grouping; report publication was refused")
                snapshots.append(snapshot)
                print(
                    f"Validated {snapshot['meta']['source_rows']:,} usage rows -> "
                    f"{snapshot['meta']['input_group_rows']:,} Power Query groups -> "
                    f"{snapshot['meta']['grouped_rows']:,} report groups", flush=True,
                )
        finally:
            con.close()
            # Release the instance's spill-directory handle before Windows removes the folder.
            del con
    incoming = mbr.combine_snapshots(snapshots) if snapshots else None
    found = set(uploads.source_scopes(incoming)) if incoming else set()
    expected = {(month, account) for month in requested_months for account in accounts}
    unexpected = found - expected
    if unexpected:
        raise ValueError(f"Unexpected enrollment/month scopes: {sorted(unexpected)}")
    if year is not None and not replace and found & set(uploads.source_scopes(history)):
        raise ValueError("Changed source overlaps loaded enrollment/month data. Review it and use --replace for a full-scope replacement.")
    result, preview = uploads.update_report(current, incoming, fx_rates=rates)
    if year is not None and not preview["comparison_changed"]:
        for field in (
            "dimensions", "rows", "row_sources", "row_publishers", "row_native_currencies",
            "row_native_costs", "row_charge_types", "calculations", "native_calculations",
            "advisor", "advisor_snapshots",
        ):
            if result.get(field) != current.get(field):
                raise ValueError(f"Historical backfill changed detailed {field}; publication was refused")
    result_history = result.get("annual_history", result)
    if year is not None:
        latest = max((month for month in result_history["dimensions"]["month"] if month.startswith(f"{year}-")), default=None)
        expected = {(month, account) for month, account in expected if latest is not None and month <= latest}
    missing = sorted(expected - set(uploads.source_scopes(result_history if year is not None else result)))
    if missing and not allow_missing and year is None:
        raise ValueError(f"Missing enrollment/month scopes: {missing}. No report change was applied.")
    if year is None and result["dimensions"]["month"] != sorted(requested_months):
        raise ValueError("Result does not contain exactly the requested two billing months")
    if year is None:
        result["audit"]["warnings"] = [
            warning for warning in result["audit"]["warnings"] if warning["code"] != "missing-usage-file"
        ]
    if missing and year is None:
        names, _discounts = _base_inputs(current)
        result["audit"]["warnings"].append({
            "code": "missing-usage-file", "title": "A requested enrollment/month usage file is missing",
            "detail": "; ".join(f"{names.get(account, account)} {month}" for month, account in missing)
            + ". No other year was relabelled, and no missing cost was treated as zero.",
        })
    report = {
        "applied": apply and bool(incoming), "basis": current["meta"]["basis"], "retained_months": result["dimensions"]["month"],
        "annual_months": result_history["dimensions"]["month"], "skipped": skipped,
        "missing_scopes": [{"month": month, "account": account} for month, account in missing],
        "source_rows": incoming["meta"]["source_rows"] if incoming else 0,
        "sources": [{"source_name": snapshot["meta"]["source_name"], "sha256": snapshot["meta"]["source_sha256"],
                     **snapshot["meta"]["raw_usage"], "fx": snapshot["meta"]["fx_details"][0]} for snapshot in snapshots],
        "preview": preview,
    }
    if apply and incoming:
        if uploads.file_digest(dashboard) != original_revision:
            raise ValueError("The live report changed during grouping; reconcile again instead of overwriting newer data")
        mbr.write_atomic(dashboard, mbr.render_html(result))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--dashboard", type=Path, default=mbr.ROOT / "data" / "mbr" / "Azure-MBR-Dashboard.html")
    parser.add_argument("--fx-file", type=Path, required=True)
    window = parser.add_mutually_exclusive_group(required=True)
    window.add_argument("--months", nargs=2)
    window.add_argument("--year", type=int, help="Incrementally backfill annual history; unchanged hashes skip grouping")
    parser.add_argument("--accounts", nargs="+", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-missing", action="store_true", help="Publish explicit gaps instead of inventing missing scopes")
    parser.add_argument("--replace", action="store_true", help="Confirm complete enrollment/month replacement in annual mode")
    args = parser.parse_args()
    files = []
    for path in args.files:
        resolved = path.resolve(strict=True)
        files.extend(sorted(resolved.glob("*.csv")) if resolved.is_dir() else [resolved])
    if not files:
        parser.error("No local usage CSVs were found")
    report = run(
        files, args.dashboard.resolve(strict=True),
        args.fx_file.resolve(strict=True), args.months, args.accounts,
        apply=args.apply, allow_missing=args.allow_missing, year=args.year, replace=args.replace,
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
