"""Verify an additional workbook against like-for-like local dashboard scopes.

No source workbook, dashboard, database, saved FX rate or calculation is replaced.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import http.client
import json
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import build_mbr_dashboard as mbr

API_QUANTUM = Decimal("0.0001")
CENT = Decimal("0.01")
GroupKey = tuple[str | None, str | None]
ScopeKey = tuple[str, str]
MODEL_ALIASES = mbr.MODEL_ALIASES


@dataclass
class SourceScope:
    month: str
    account: str
    enrollment: str
    rows: int = 0
    native: Decimal = Decimal(0)
    gbp: Decimal = Decimal(0)
    eligible_gbp: Decimal = Decimal(0)
    groups: dict[GroupKey, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    usage: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    portfolio: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    allocation: dict[tuple[str, str, str], Decimal] = field(
        default_factory=lambda: defaultdict(Decimal),
    )


def index_source(rows: Iterable[Mapping[str, object]]) -> dict[ScopeKey, SourceScope]:
    result: dict[ScopeKey, SourceScope] = {}
    months: dict[str, str] = {}
    with localcontext() as ctx:
        ctx.prec = 80
        for number, row in enumerate(rows, 2):
            if all(not mbr.text(row.get(name)) for name in mbr.SOURCE_FIELDS):
                continue
            value = mbr.text(row["BillingPeriodStartDate"])
            if value not in months:
                months[value] = mbr.excel_date(value, f"data!B{number}", month=True)
            month = months[value]
            account = mbr.text(row["BillingAccountId"]).removesuffix(".0")
            if not account or not mbr.text(row["Enrolment"]):
                raise ValueError(f"data!{number}: missing enrollment identity")
            name = mbr.enrollment(row["Enrolment"])
            scope = result.setdefault((month, account), SourceScope(month, account, name))
            if scope.enrollment != name:
                raise ValueError(f"data!{number}: conflicting names for the same enrollment")
            native = mbr.decimal_value(row["Cost"], f"data!O{number}")
            gbp = mbr.decimal_value(row["Cost GBP"], f"data!P{number}")
            service = mbr.text(row["MeterCategory"]) or None
            raw_model = mbr.text(row["PricingModel"])
            model = MODEL_ALIASES.get(raw_model, raw_model) or None
            scope.native += native
            scope.gbp += gbp
            scope.rows += 1
            scope.groups[service, model] += native
            scope.usage[mbr.text(row["Usage Type"])] += gbp
            scope.portfolio[mbr.text(row["Sub Type"])] += gbp
            if mbr.text(row["IsAzureCreditEligible"]).lower() == "true":
                scope.eligible_gbp += gbp
            scope.allocation[service or "", model or "", mbr.text(row["Utilization Type"])] += native
    if not result:
        raise ValueError("Reference contains no populated cost scopes")
    return result


def compare_amounts(expected: Decimal, actual: Decimal) -> dict[str, Any]:
    with localcontext() as ctx:
        ctx.prec = 80
        return {
            "matched": expected.quantize(API_QUANTUM, rounding=ROUND_HALF_EVEN)
            == actual.quantize(API_QUANTUM, rounding=ROUND_HALF_EVEN),
            "reference": expected, "dashboard": actual, "difference": expected - actual,
            "penny_rounding_boundary": expected.quantize(CENT, rounding=ROUND_HALF_EVEN)
            != actual.quantize(CENT, rounding=ROUND_HALF_EVEN),
        }


def compare_scope(
    source: SourceScope, response: Mapping[str, Any], currency: str,
    adjustment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if response["group_by"] != ["service", "pricing_model"]:
        raise ValueError("Comparison needs service and pricing_model, in that order")
    if len(response["rows"]) != response["groups"]:
        raise ValueError("Incomplete Explorer pagination; no truncated comparison is permitted")
    app: dict[GroupKey, Decimal] = {}
    for row in response["rows"]:
        service, raw_model = row["keys"]
        key = (service, MODEL_ALIASES.get(raw_model, raw_model))
        if key in app:
            raise ValueError("Pricing aliases collide in API groups; review before comparing")
        app[key] = mbr.decimal_value(row["cost"], "Explorer cost")
    comparisons = []
    extras = []
    with localcontext() as ctx:
        ctx.prec = 80
        for key in sorted(source.groups.keys() | app.keys(), key=str):
            expected, actual = source.groups.get(key), app.get(key)
            if expected is not None and actual is not None:
                comparison = compare_amounts(expected, actual)
                comparisons.append({"service": key[0], "pricing_model": key[1], **comparison})
            else:
                present = expected if expected is not None else actual
                extras.append({
                    "service": key[0], "pricing_model": key[1],
                    "reference": expected, "dashboard": actual,
                    "status": "zero_only_group" if present == 0 else "scope_difference",
                    "present_in": "workbook" if expected is not None else "dashboard",
                })
        total = compare_amounts(source.native, mbr.decimal_value(response["total"], "Explorer total"))
        status = "matched" if total["matched"] else "different"
        detail = "Native signed totals agree at the API's four-decimal precision."
        nonzero_extras = [row for row in extras if row["status"] != "zero_only_group"]
        adjusted = None
        if nonzero_extras and adjustment is not None:
            confirmed = (
                adjustment["group_by"] == ["charge_type", "publisher_type"]
                and all(row["keys"][0] == "RoundingAdjustment" for row in adjustment["rows"])
                and len(adjustment["rows"]) == adjustment["groups"]
                and bool(adjustment["rows"])
            )
            extra_total = sum(
                (row["dashboard"] for row in nonzero_extras if row["dashboard"] is not None),
                Decimal(0),
            )
            only_adjustments = all(
                row["service"] == "RoundingAdjustment" and row["reference"] is None
                for row in nonzero_extras
            )
            adjustment_total = mbr.decimal_value(adjustment["total"], "Adjustment total")
            if confirmed and only_adjustments and compare_amounts(extra_total, adjustment_total)["matched"]:
                adjusted = compare_amounts(source.native + adjustment_total, total["dashboard"])
                if adjusted["matched"] and all(row["matched"] for row in comparisons):
                    status = "explained_difference"
                    detail = (
                        "The app includes signed RoundingAdjustment charges absent from the workbook. "
                        "Adding those recorded charges reconciles at API precision. Their inclusion is "
                        "explained, not independent proof of their provenance; nothing was removed or changed."
                    )
        different_groups = sum(not row["matched"] for row in comparisons)
        if different_groups or (nonzero_extras and status != "explained_difference"):
            status = "different"
            detail = "A non-zero group or shared-group difference remains for review."
        return {
            "month": source.month, "account": source.account, "enrollment": source.enrollment,
            "currency": currency, "source_rows": source.rows, "status": status, "detail": detail,
            "total": total, "shared_groups": len(comparisons),
            "matched_groups": len(comparisons) - different_groups,
            "comparisons": comparisons, "extra_groups": extras,
            "adjustment_evidence": adjustment, "adjusted_comparison": adjusted,
        }


def native_currency(metadata: Mapping[str, Any], account: str, basis: str) -> str:
    currencies = metadata["billing_account_currencies"].get(account, [])
    if len(currencies) != 1:
        raise ValueError(f"Enrollment {account}: native currency is missing or ambiguous")
    currency = currencies[0]
    compatible = [
        row for row in metadata["scopes"]
        if row["currency"] == currency and row["cost_kind"] == basis
    ]
    if not compatible or any(row["synthetic"] for row in compatible):
        raise ValueError(f"Enrollment {account}: no non-synthetic matching cost basis")
    return currency


class LocalApi:
    def __init__(self, base: str) -> None:
        parsed = urlsplit(base)
        if (
            parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment
        ):
            raise ValueError("Verification API must be an HTTP loopback origin without credentials or a path")
        self.host = parsed.hostname
        self.port = parsed.port or 8765
        self.base = base.rstrip("/")

    def get(self, route: str, params: list[tuple[str, str]] | None = None) -> Any:
        if route not in {"/api/meta", "/api/cost/explore", "/api/cost/series"}:
            raise ValueError("Only read-only verification endpoints are allowed")
        connection = http.client.HTTPConnection(self.host, self.port, timeout=40)
        path = route + ("?" + urlencode(params) if params else "")
        try:
            connection.request("GET", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read(4 * 1024 * 1024 + 1)
            if response.status != 200:
                raise RuntimeError(f"Local API {route}: HTTP {response.status}: {body[:500]!r}")
            if len(body) > 4 * 1024 * 1024:
                raise ValueError("Local API response exceeded the verification size limit")
            return json.loads(body, parse_float=Decimal)
        finally:
            connection.close()

    def explore(self, params: list[tuple[str, str]], groups: tuple[str, str]) -> dict[str, Any]:
        common = params + [("group_by", group) for group in groups] + [("page_size", "200")]
        first = self.get("/api/cost/explore", common + [("page", "1")])
        pages = first["pages"]
        if not isinstance(pages, int) or not 1 <= pages <= 200:
            raise ValueError("Explorer page count is outside the bounded verification limit")
        rows = list(first["rows"])
        for page in range(2, pages + 1):
            response = self.get("/api/cost/explore", common + [("page", str(page))])
            if (
                response["page"] != page or response["groups"] != first["groups"]
                or response["total"] != first["total"] or response["pages"] != pages
                or response["group_by"] != first["group_by"]
            ):
                raise ValueError("Explorer data changed during pagination; verification was not published")
            rows.extend(response["rows"])
        if len(rows) != first["groups"]:
            raise ValueError("Incomplete Explorer pagination")
        return {**first, "rows": rows}


def snapshot_totals(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], Decimal]:
    if snapshot["version"] != 1 or snapshot["scale"] != mbr.SCALE:
        raise ValueError("Unsupported standalone snapshot")
    result: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    order = snapshot["dimension_order"]
    month_index, name_index = order.index("month"), order.index("enrollment")
    with localcontext() as ctx:
        ctx.prec = 80
        for row in snapshot["rows"]:
            month = snapshot["dimensions"]["month"][row[month_index]]
            name = snapshot["dimensions"]["enrollment"][row[name_index]]
            accounts = snapshot["meta"]["billing_accounts"].get(name, [])
            if len(accounts) != 1:
                raise ValueError("Standalone enrollment identity is ambiguous")
            result[month, accounts[0]] += Decimal(row[len(order)]) / mbr.FACTOR
    return result


def compare_snapshots(reference: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    reference_totals, current_totals = snapshot_totals(reference), snapshot_totals(current)
    common = sorted(reference_totals.keys() & current_totals.keys())
    base = {
        "reference_months": reference["dimensions"]["month"],
        "dashboard_months": current["dimensions"]["month"],
        "reference_advisor_dates": [
            reference["meta"]["advisor_date_start"], reference["meta"]["advisor_date_end"],
        ],
        "dashboard_advisor_dates": [
            current["meta"]["advisor_date_start"], current["meta"]["advisor_date_end"],
        ],
    }
    if reference["meta"]["source_sha256"] in {
        current["meta"]["source_sha256"],
        *(source["source_sha256"] for source in current["meta"].get("sources", [])),
    }:
        return {**base, "status": "same_source", "detail": "The same workbook is not an independent second source.", "checks": []}
    if any(
        reference["meta"][key] != current["meta"][key]
        for key in ("basis", "currency", "currency_label")
    ):
        return {**base, "status": "not_comparable", "detail": "Cost basis or saved conversion policy differs; no silent FX conversion.", "checks": []}
    if not common:
        return {**base, "status": "not_comparable", "detail": "No shared billing-month/enrollment scopes. The existing dashboard data was not replaced or verified by non-overlapping months.", "checks": []}
    checks = [
        {"month": key[0], "account": key[1], **compare_amounts(reference_totals[key], current_totals[key])}
        for key in common
    ]
    return {
        **base, "status": "matched" if all(row["matched"] for row in checks) else "different",
        "detail": "Only intersecting scopes were compared using the same basis and saved GBP conversion policy.",
        "checks": checks,
    }


def formula_checks(profile: Mapping[str, Any], scopes: Mapping[ScopeKey, SourceScope]) -> list[dict[str, Any]]:
    result = []
    latest = max(month for month, _account in scopes)
    previous_year, previous_month = map(int, latest.split("-"))
    previous = f"{previous_year - (previous_month == 1):04d}-{12 if previous_month == 1 else previous_month - 1:02d}"
    names = {scope.enrollment: scope.account for scope in scopes.values()}

    def check(sheet_name: str, row: int, column: int, expected: Decimal | None, definition: str) -> None:
        sheet = profile["sheets"].get(sheet_name)
        if sheet is None:
            return
        actual = mbr.cell(sheet, row, column)
        address = f"{sheet_name}!{chr(64 + column)}{row}"
        formulas = sheet.get("formulas", [])
        formula = (
            formulas[row - sheet["start_row"]][column - sheet["start_column"]]
            if formulas else None
        )
        if expected is None or actual is None or (isinstance(actual, int) and actual in mbr.ERRORS):
            result.append({"cell": address, "status": "unavailable", "definition": definition, "formula": formula})
            return
        value = mbr.decimal_value(actual, address)
        is_ratio = "ratio" in definition.lower()
        matched = abs(expected - value) <= Decimal("1e-12") if is_ratio else compare_amounts(expected, value)["matched"]
        result.append({
            "cell": address, "status": "recomputed" if matched else "different",
            "definition": definition, "reference": expected, "cached_cell": value, "formula": formula,
        })

    with localcontext() as ctx:
        ctx.prec = 80
        selected = profile["sheets"].get("Command Panel")
        selected_month = None
        if selected:
            try:
                selected_month = mbr.excel_date(
                    mbr.cell(selected, 2, 3), "Command Panel!C2", month=True,
                )
            except ValueError as exc:
                result.append({
                    "cell": "Command Panel!C2", "status": "unavailable",
                    "definition": f"The selected summary date is invalid: {exc}",
                })
        credit_sheet = profile["sheets"].get("Credits")
        credit_months = {
            mbr.excel_date(value, f"Credits!{chr(64 + column)}11", month=True)
            for column in range(3, 15)
            if credit_sheet and (value := mbr.cell(credit_sheet, 11, column)) is not None
        }
        def enrollment_column(sheet: Mapping[str, Any] | None, name: str) -> int | None:
            if sheet is None:
                return None
            columns = {
                sheet["start_column"] + column
                for row in sheet["values"][:4] for column, value in enumerate(row)
                if mbr.text(value).casefold() == name.casefold()
            }
            return next(iter(columns)) if len(columns) == 1 else None

        def enrollment_row(sheet: Mapping[str, Any] | None, name: str) -> int | None:
            if sheet is None:
                return None
            rows = {
                sheet["start_row"] + row
                for row, values in enumerate(sheet["values"])
                if any(mbr.text(value).casefold() == name.casefold() for value in values[:3])
            }
            return next(iter(rows)) if len(rows) == 1 else None

        for name, account in sorted(names.items()):
            scope = scopes.get((latest, account)) if account else None
            if scope is None:
                continue
            first = profile["sheets"].get("First Page")
            column = enrollment_column(first, name)
            if first and column is not None and selected_month == latest:
                check("First Page", 5, column, scope.gbp, "Selected-month signed Cost GBP, independently summed from populated data rows.")
                for row, category in ((7, "Azure"), (8, "Market Place")):
                    check("First Page", row, column, scope.usage.get(category, Decimal(0)), f"Selected-month Cost GBP using workbook Usage Type = {category}; classification itself is not independently certified.")
                for row, portfolio in ((9, "Greenfield"), (10, "Brownfield")):
                    check("First Page", row, column, scope.portfolio.get(portfolio, Decimal(0)), f"Selected-month Cost GBP using source Sub Type = {portfolio}; not application-tag attribution.")
                prior = scopes.get((previous, account))
                ratio = scope.gbp / prior.gbp - 1 if prior and prior.gbp else None
                check("First Page", 5, column + 1, ratio, "Month-on-month ratio from the two available source months; not proof of export completeness.")
            eligible = sum(
                (s.eligible_gbp for s in scopes.values() if s.account == account and s.month in credit_months),
                Decimal(0),
            ) if credit_months else None
            credits_row = enrollment_row(credit_sheet, name)
            if credits_row is not None:
                check("Credits", credits_row, 17, eligible, "Credit-eligible Cost GBP across available months in the credit table only. Matching a YTD-labelled cell does not establish full-year coverage or actual credit consumption.")
            utilization_column = enrollment_column(profile["sheets"].get("Savings and Utilization Rate"), name)
            if utilization_column is None:
                continue
            for sheet_row, model, only_vm in ((11, "SavingsPlan", True), (12, "Reservation", False)):
                selected_allocations = [
                    (key, value) for key, value in scope.allocation.items()
                    if key[1] == model and (not only_vm or key[0] == "Virtual Machines")
                ]
                denominator = sum((value for _key, value in selected_allocations), Decimal(0))
                numerator = sum((value for key, value in selected_allocations if key[2] == "Used"), Decimal(0))
                ratio = numerator / denominator if denominator else None
                check("Savings and Utilization Rate", sheet_row, utilization_column, ratio,
                      f"{model} used-cost / total-cost ratio on the workbook's category scope; cost-weighted, not hourly utilization.")
    return result


def coverage(standalone: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"workspace": name, "status": status, "evidence": detail}
        for name, status, detail in (
            ("Portfolio Overview", "partial", "Native monthly totals for matched scopes; differences retain signed adjustments. Budgets, forecasts and full-year totals are not certified."),
            ("Cost Explorer", "partial", "Every common service/pricing-model group is compared. Subscription IDs, resources, tags and other drill-downs are not independently verified."),
            ("Cost analysis", "partial", "Source-overlapping monthly native cost only. Billing-period rows cannot verify daily trends, peaks or all comparison presets."),
            ("Spend / Charge Analysis", "partial", "Pricing-model cost is checked. The workbook lacks an explicit charge-type column; charge classification is not independently certified."),
            ("Applications", "not_comparable", "No application tags or resource IDs. Greenfield/Brownfield naming rules are not application attribution."),
            ("AI & Database", "partial", "Underlying service-category cost only; workload-family rules and workload-specific ratios remain unverified."),
            ("Commitments & Savings", "not_comparable", "Workbook utilization is cost-weighted, not hours. Savings aliases and unused-benefit currency branches are unsafe; no realized-saving or coverage certification."),
            ("Optimization", "not_comparable", "No resource-lifetime history, consistent hourly demand, utilization telemetry or resource-ID evidence for purchase/rightsizing advice."),
            ("Pricing & Reference Data", "not_comparable", "No meter IDs, offers or billing units for price-sheet matching. The numeric Unit column is not evidence of unit-of-measure or retail-price provenance."),
            ("Data Management", "partial", "Workbook hash, row counts, source formulas and source-to-snapshot reconciliation are recorded; not proof of export-partition completeness or retention correctness."),
            ("Knowledge", "reference_only", "Definitions and source limitations are documented; documentation is not numerical verification."),
            ("Standalone MBR spend", standalone["status"], standalone["detail"]),
            ("Standalone Advisor estimates", "not_comparable", "Recommendation-ID/as-of alignment is not certified. Captured Advisor dates are separate from spend periods. Missing mappings/GBP values remain explicit; estimates may overlap."),
            ("FX / Combined view", "not_comparable", "The workbook uses a fixed saved GBP conversion; the app uses saved monthly FX. Native amounts are compared without overwriting rates."),
            ("Credits / YTD / forecasts", "not_comparable", "Available eligible spend is not a full-year credit ledger. Contract drawdown, remaining balance, MACC burn and annual projections need additional evidence."),
        )
    ]


def read_dashboard(path: Path) -> dict[str, Any]:
    return mbr.read_dashboard(path)


def json_text(value: object) -> str:
    def encode(item: object) -> str:
        if isinstance(item, Decimal):
            return format(item, "f")
        raise TypeError(f"Cannot serialize {type(item).__name__}")
    return json.dumps(value, default=encode, ensure_ascii=True, indent=2, allow_nan=False)


def render_report(report: Mapping[str, Any]) -> str:
    template = (mbr.ASSETS / "dashboard.html").read_text(encoding="utf-8")
    head, separator, _body = template.partition("</head>")
    if not separator:
        raise ValueError("Shared report theme has no head boundary")
    head = re.sub(r"<title>[^<]*</title>", "<title>MBR second-source verification</title>", head)
    checks = report["cost_checks"]
    shared = sum(row["shared_groups"] for row in checks)
    matched = sum(row["matched_groups"] for row in checks)

    def money(value: Decimal | None, currency: str) -> str:
        if value is None:
            return "not present"
        with localcontext() as ctx:
            ctx.prec = 80
            rounded = value.quantize(API_QUANTUM, rounding=ROUND_HALF_EVEN)
            if rounded == 0:
                rounded = rounded.copy_abs()
            return escape(f"{currency} {rounded:,.4f}")

    def badge(status: str) -> str:
        return '<span class="verification-status">' + escape(status.replace("_", " ")) + "</span>"

    monthly = "".join(
        "<tr><td>" + escape(row["month"] + " / " + row["enrollment"])
        + '</td><td class="num">' + money(row["total"]["reference"], row["currency"])
        + '</td><td class="num">' + money(row["total"]["dashboard"], row["currency"])
        + '</td><td class="num">' + money(row["total"]["difference"], row["currency"])
        + "</td><td>" + badge(row["status"]) + "</td></tr>" for row in checks
    )
    exceptions = []
    for scope in checks:
        for row in scope["extra_groups"]:
            exceptions.append(
                "<tr><td>" + escape(scope["month"] + " / " + scope["enrollment"])
                + "</td><td>" + escape(str(row["service"]) + " / " + str(row["pricing_model"]))
                + '</td><td class="num">' + money(row["reference"], scope["currency"])
                + '</td><td class="num">' + money(row["dashboard"], scope["currency"])
                + "</td><td>" + escape(row["status"].replace("_", " ")) + "</td></tr>"
            )
        for row in scope["comparisons"]:
            if not row["matched"] or row["penny_rounding_boundary"]:
                exceptions.append(
                    "<tr><td>" + escape(scope["month"] + " / " + scope["enrollment"])
                    + "</td><td>" + escape(str(row["service"]) + " / " + str(row["pricing_model"]))
                    + '</td><td class="num">' + money(row["reference"], scope["currency"])
                    + '</td><td class="num">' + money(row["dashboard"], scope["currency"])
                    + "</td><td>" + ("API precision matches; penny re-rounding boundary" if row["matched"] else "unexplained difference") + "</td></tr>"
                )
    adjustments = "".join(
        '<p class="note">' + escape(row["month"] + " / " + row["enrollment"] + ": " + row["detail"]) + "</p>"
        for row in checks if row["status"] != "matched"
    )
    matrix = "".join(
        "<tr><td>" + escape(row["workspace"]) + "</td><td>" + badge(row["status"])
        + "</td><td>" + escape(row["evidence"]) + "</td></tr>" for row in report["coverage"]
    )
    formulas = "".join(
        "<tr><td>" + escape(row["cell"]) + "</td><td>" + badge(row["status"])
        + "</td><td>" + escape(row["definition"]) + "<details><summary>Source formula</summary><code>"
        + escape(str(row.get("formula") or "Not supplied")) + "</code></details></td></tr>"
        for row in report["formula_checks"]
    )
    warnings = "".join(
        "<li><strong>" + escape(row["title"]) + '</strong><p class="note">'
        + escape(row["detail"]) + "</p></li>" for row in report["reference"]["warnings"]
    )
    source = report["reference"]
    source_details = "".join(
        "<dt>" + escape(label) + "</dt><dd>" + escape(str(value)) + "</dd>"
        for label, value in (
            ("Workbook", source["source_name"]), ("SHA-256", source["source_sha256"]),
            ("Extracted UTC", source["extracted_utc"]), ("Verified UTC", report["verified_utc"]),
            ("Billing months", ", ".join(source["months"])), ("Basis", source["basis"]),
            ("Saved reporting conversion", source["currency_label"]),
            ("Native currency identity", source["native_currency_provenance"]),
            ("Reference Advisor dates", " to ".join(value or "not supplied" for value in report["standalone"]["reference_advisor_dates"])),
            ("Standalone Advisor dates", " to ".join(value or "not supplied" for value in report["standalone"]["dashboard_advisor_dates"])),
            ("API", report["api"]), ("Standalone source", report["standalone"]["source_name"]),
            ("Comparison precision", "Four decimal places, matching the API's money contract. Native currencies stay separate."),
        )
    )
    derived_formulas = "".join(
        "<details><summary>" + escape(name) + '</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'
        + escape(formula) + "</pre></details>"
        for name, formula in source["derived_column_formulas"].items()
    )
    return head + """</head><body>
<header class="topbar"><div class="brand"><div class="brandmark">MBR</div><div><strong>Second-source verification</strong><small>Evidence, differences and limits</small></div></div><div class="actions"><button id="theme">Light / dark</button><button class="primary" id="print">Print / Save PDF</button></div></header>
<main style="max-width:1480px;margin:auto">
<div class="page-heading"><div><p class="eyebrow">Reference evidence, not blanket approval</p><h1>Workbook cross-check</h1><p class="muted">No workbook, live dashboard, database, exchange rate or signed adjustment was replaced.</p></div></div>
<div class="banner"><div><strong>Verified only where the evidence is comparable</strong><p>Unlisted calculations remain unverified. A source formula matching its own rows is not independent proof of invoice completeness, credit consumption or realized savings.</p></div></div>
<div class="kpis">""" + (
        f'<article class="card kpi hero"><div class="kpi-label">Populated reference rows</div><div class="kpi-value">{source["rows"]:,}</div><p class="note">Source values preserved; missing scopes stay explicit</p></article>'
        f'<article class="card kpi"><div class="kpi-label">Shared service / pricing groups</div><div class="kpi-value">{matched:,} / {shared:,}</div><p class="note">Matched at API precision; native currencies separate</p></article>'
        f'<article class="card kpi"><div class="kpi-label">Native total matches (4dp)</div><div class="kpi-value">{sum(r["status"] == "matched" for r in checks)} / {len(checks)}</div><p class="note">Before separately explaining signed adjustments</p></article>'
        f'<article class="card kpi"><div class="kpi-label">Explained total differences</div><div class="kpi-value">{sum(r["status"] == "explained_difference" for r in checks)}</div><p class="note">Adjustments remain in the application totals</p></article>'
    ) + """</div>
<article class="card" style="margin-bottom:20px"><div class="card-heading"><div><h2>Native monthly cost reconciliation</h2><p class="note">Difference = workbook minus dashboard. GBP-converted workbook values are not compared to a different FX policy.</p></div></div>
<div class="table-wrap"><table class="data-table"><thead><tr><th>Month / enrollment</th><th class="num">Workbook native</th><th class="num">Dashboard native</th><th class="num">Difference</th><th>Status</th></tr></thead><tbody>""" + monthly + """</tbody></table></div>""" + adjustments + """</article>
<article class="card" style="margin-bottom:20px"><h2>Exceptions and precision</h2><p class="note">An absent group is shown as not present, never silently valued at zero. API serialization can erase which side of a half-penny the full-precision amount lies on; that is identified separately from a cost mismatch.</p>
<div class="table-wrap"><table class="data-table"><thead><tr><th>Scope</th><th>Service / pricing</th><th class="num">Workbook</th><th class="num">Dashboard</th><th>Explanation</th></tr></thead><tbody>""" + "".join(exceptions) + """</tbody></table></div></article>
<article class="card" style="margin-bottom:20px"><div class="card-heading"><div><h2>Every workspace: what this source can verify</h2><p class="note">Partial verification does not mean every card, filter, ratio or drill-down on that workspace is certified.</p></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Workspace / calculation</th><th>Status</th><th>Evidence and remaining limits</th></tr></thead><tbody>""" + matrix + """</tbody></table></div></article>
<div class="grid-2"><article class="card"><h2>Source and handling</h2><dl class="source-grid">""" + source_details + """</dl></article><article class="card"><h2>Reference limitations</h2><ul class="quality-list" style="margin-top:16px">""" + warnings + """</ul><h3 style="margin-top:20px">Sampled source-column formulas</h3>""" + derived_formulas + """</article></div>
<article class="card"><h2>Workbook formula cross-checks</h2><p class="note">These checks recompute selected cost, portfolio, eligibility and cost-weighted ratios from the workbook rows. They do not promote the workbook into financial ground truth. Invalid savings formulas and unsupported forecasts remain excluded.</p><div class="table-wrap"><table class="data-table"><thead><tr><th>Source cell</th><th>Status</th><th>Definition / source formula</th></tr></thead><tbody>""" + formulas + """</tbody></table></div></article>
<p class="report-footnote">Private local reference evidence. HTML, JSON and normalized snapshots contain client financial data and do not inherit workbook encryption or sensitivity protection. Keep them outside version control and public hosting. Results are point-in-time, not a live certification.</p>
</main><script>
document.getElementById("theme").addEventListener("click", () => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; });
document.getElementById("print").addEventListener("click", () => { document.documentElement.dataset.beforePrintTheme = document.documentElement.dataset.theme; document.documentElement.dataset.theme = "light"; window.print(); });
window.addEventListener("afterprint", () => { if (document.documentElement.dataset.beforePrintTheme) { document.documentElement.dataset.theme = document.documentElement.dataset.beforePrintTheme; delete document.documentElement.dataset.beforePrintTheme; } });
</script></body></html>"""


def verify(
    extraction: Path, dashboard: Path, api: LocalApi, source_path: Path | None,
    output: Path,
) -> dict[str, Any]:
    protected = {dashboard.resolve()}
    if source_path is not None:
        protected.add(source_path.resolve())
    if any((output / name).resolve() in protected for name in (
        "Reference-Snapshot.json", "Reference-Verification.json", "Reference-Verification.html",
    )):
        raise ValueError("Verification output would replace an input; choose another output directory")
    catalog_path = mbr.ROOT / "data" / "mbr" / "references" / "index.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {"version": 1, "references": []}
    if catalog["version"] != 1 or not isinstance(catalog["references"], list):
        raise ValueError("Unsupported reference catalog; existing entries were not replaced")
    profile = json.loads((extraction / "mbr-profile.json").read_text(encoding="utf-8"), parse_float=Decimal)
    if source_path is not None:
        with source_path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != profile["source_sha256"]:
            raise ValueError("Source hash no longer matches the extraction; verification was not published")
    with (extraction / "mbr-data.csv").open(encoding="utf-8", newline="") as handle:
        snapshot = mbr.build_snapshot(profile, csv.DictReader(handle))
    with (extraction / "mbr-data.csv").open(encoding="utf-8", newline="") as handle:
        scopes = index_source(csv.DictReader(handle))
    current = read_dashboard(dashboard)
    metadata = api.get("/api/meta")
    if metadata["recovery_required"]:
        raise ValueError("Application aggregate recovery is required; no stale figures will be verified")
    checks = []
    captures = []
    for (month, account), scope in sorted(scopes.items()):
        currency = native_currency(metadata, account, snapshot["meta"]["basis"])
        year, number = map(int, month.split("-"))
        params = [
            ("cost_kind", snapshot["meta"]["basis"]), ("currency", currency),
            ("billing_account", account), ("start", month + "-01"),
            ("end", f"{month}-{calendar.monthrange(year, number)[1]:02d}"), ("combined", "false"),
        ]
        response = api.explore(params, ("service", "pricing_model"))
        series = api.get("/api/cost/series", params + [("grain", "month")])
        if len(series) != 1 or series[0]["period"] != month + "-01" or series[0]["total"] != response["total"]:
            raise ValueError("Monthly series and Explorer totals changed or disagree; verification was not published")
        extra_adjustments = any(
            row["keys"][0] == "RoundingAdjustment"
            and ("RoundingAdjustment", MODEL_ALIASES.get(row["keys"][1], row["keys"][1])) not in scope.groups
            for row in response["rows"]
        )
        adjustment = api.explore(
            params + [("service", "RoundingAdjustment")], ("charge_type", "publisher_type"),
        ) if extra_adjustments else None
        checks.append(compare_scope(scope, response, currency, adjustment))
        captures.append({"month": month, "account": account, "currency": currency, "explorer": response, "monthly_series": series})
    standalone_check = compare_snapshots(snapshot, current)
    sample_formulas = profile.get("data_formulas", [])
    derived_formulas = {
        name: formula for name, formula in zip(
            profile.get("data_headers", []), sample_formulas[1] if len(sample_formulas) > 1 else [],
        )
        if isinstance(formula, str) and formula.startswith("=")
    }
    report = {
        "version": 1, "verified_utc": datetime.now(UTC).isoformat(), "api": api.base,
        "default_for_unlisted_metrics": "unverified",
        "reference": {
            "source_name": profile["source_name"], "source_sha256": profile["source_sha256"],
            "source_path": str(source_path) if source_path else None,
            "extracted_utc": profile["extracted_utc"], "basis": snapshot["meta"]["basis"],
            "currency_label": snapshot["meta"]["currency_label"], "rows": snapshot["meta"]["source_rows"],
            "months": snapshot["dimensions"]["month"], "warnings": snapshot["audit"]["warnings"],
            "source_reconciliations": snapshot["audit"]["reconciliations"],
            "derived_column_formulas": derived_formulas,
            "summary_excel_errors": snapshot["audit"]["excel_errors"],
            "native_currency_provenance": "Explicit single-currency enrollment mapping from the captured application metadata; the workbook has no native-currency column.",
        },
        "standalone": {
            "source_name": current["meta"]["source_name"],
            "source_sha256": current["meta"]["source_sha256"], **standalone_check,
        },
        "cost_checks": checks, "formula_checks": formula_checks(profile, scopes),
        "coverage": coverage(standalone_check), "api_metadata": metadata, "api_captures": captures,
    }
    output.mkdir(parents=True, exist_ok=True)
    snapshot_content = json_text(snapshot)
    snapshot_path = output / "Reference-Snapshot.json"
    mbr.write_atomic(snapshot_path, snapshot_content)
    with snapshot_path.open("rb") as handle:
        report["reference"]["normalized_snapshot_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    mbr.write_atomic(output / "Reference-Verification.json", json_text(report))
    mbr.write_atomic(output / "Reference-Verification.html", render_report(report))
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog["references"] = [
        row for row in catalog["references"] if row["source_sha256"] != profile["source_sha256"]
    ] + [{
        "source_name": profile["source_name"], "source_sha256": profile["source_sha256"],
        "source_path": str(source_path) if source_path else None, "months": snapshot["dimensions"]["month"],
        "verified_utc": report["verified_utc"], "verification": str(output / "Reference-Verification.json"),
        "snapshot": str(output / "Reference-Snapshot.json"),
        "default_for_unlisted_metrics": "unverified",
    }]
    mbr.write_atomic(catalog_path, json_text(catalog))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--dashboard", type=Path, default=mbr.ROOT / "data" / "mbr" / "Azure-MBR-Dashboard.html")
    parser.add_argument("--api", default="http://127.0.0.1:8765")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--from-extract", type=Path, help="Reuse a local extraction; source SHA-256 is rechecked")
    args = parser.parse_args()
    source = args.workbook.resolve(strict=True)
    dashboard = args.dashboard.resolve(strict=True)
    output = (args.output_dir or mbr.ROOT / "data" / "mbr" / "references" / source.stem).resolve()
    output.mkdir(parents=True, exist_ok=True)
    api = LocalApi(args.api)
    if args.from_extract:
        report = verify(args.from_extract.resolve(strict=True), dashboard, api, source, output)
    else:
        with tempfile.TemporaryDirectory(prefix=".reference-extract-", dir=output) as directory:
            extraction = Path(directory)
            mbr.extract_workbook(source, extraction)
            report = verify(extraction, dashboard, api, source, output)
    print(f"Verification report: {output / 'Reference-Verification.html'}")
    print("Native total checks:", dict(Counter(row["status"] for row in report["cost_checks"])))
    print(
        "Shared service/pricing groups:",
        sum(row["matched_groups"] for row in report["cost_checks"]), "/",
        sum(row["shared_groups"] for row in report["cost_checks"]),
    )
    print("Standalone comparison:", report["standalone"]["status"], "-", report["standalone"]["detail"])
    print("Formula checks:", dict(Counter(row["status"] for row in report["formula_checks"])))
    if (
        any(row["status"] == "different" for row in report["cost_checks"])
        or any(row["status"] == "different" for row in report["formula_checks"])
        or report["standalone"]["status"] == "different"
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
