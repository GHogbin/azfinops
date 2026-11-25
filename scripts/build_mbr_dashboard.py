"""Build an offline MBR dashboard without changing the source workbook.

Refresh on Windows with desktop Excel and pywin32:
    python scripts\\build_mbr_dashboard.py 'C:\\path\\report.xlsb'
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "reporting" / "mbr"
SCALE = 18
FACTOR = 10**SCALE
QUANTUM = Decimal("1e-18")
CENT = Decimal("0.01")
MISSING = "(not supplied)"
MODEL_ALIASES = {
    "On Demand": "OnDemand", "OnDemand": "OnDemand",
    "Savings Plan": "SavingsPlan", "SavingsPlan": "SavingsPlan",
    "Reservation": "Reservation", "Spot": "Spot",
}
DIMENSIONS = (
    ("month", "BillingPeriodStartDate"),
    ("enrollment", "Enrolment"),
    ("service", "MeterCategory"),
    ("subscription", "SubscriptionName"),
    ("region", "ResourceLocation"),
    ("pricing", "PricingModel"),
    ("portfolio", "Sub Type"),
    ("allocation", "Utilization Type"),
    ("eligible", "IsAzureCreditEligible"),
    ("usage", "Usage Type"),
)
SOURCE_FIELDS = (
    "Source.Name", "BillingPeriodStartDate", "BillingAccountId", "MeterCategory", "Product",
    "SubscriptionName", "IsAzureCreditEligible", "ProductOrderName", "PublisherType",
    "PricingModel", "ResourceLocation", "Effective", "Unit", "Quantity", "Cost",
)
ERRORS = {
    -2146826288: "#NULL!", -2146826281: "#DIV/0!", -2146826273: "#VALUE!",
    -2146826265: "#REF!", -2146826259: "#NAME?", -2146826252: "#NUM!",
    -2146826246: "#N/A", -2146826245: "#GETTING_DATA",
}


def text(value: object) -> str:
    return "" if value is None else str(value).strip()


def decimal_value(value: object, location: str, *, thousands: bool = False) -> Decimal:
    raw = text(value)
    if thousands and re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
        raw = raw.replace(",", "")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{location}: expected an amount, got {raw!r}") from exc
    if not amount.is_finite() or amount.copy_abs() >= Decimal("1e20"):
        raise ValueError(f"{location}: amount is not a finite DECIMAL(38,18)")
    return amount


def units(value: object, location: str) -> int:
    with localcontext() as ctx:
        ctx.prec = 80
        amount = decimal_value(value, location).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
        if abs(amount) >= Decimal("1e20"):
            raise ValueError(f"{location}: rounded amount exceeds DECIMAL(38,18)")
        return int(amount * FACTOR)


def amount_string(value: int) -> str:
    with localcontext() as ctx:
        ctx.prec = 80
        return format(Decimal(value) / FACTOR, "f")


def excel_date(value: object, location: str, *, month: bool = False) -> str:
    raw = text(value)
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[ T])", raw):
        result = date.fromisoformat(raw[:10])
    else:
        serial = decimal_value(value, location)
        if not Decimal(1) <= serial <= Decimal(2958465):
            raise ValueError(f"{location}: invalid Excel date {raw!r}")
        if month and serial != serial.to_integral_value():
            raise ValueError(f"{location}: billing period is not a whole date")
        result = date(1899, 12, 30) + timedelta(days=int(serial))
    if month:
        if result.day != 1:
            raise ValueError(f"{location}: expected a billing-month start")
        return result.isoformat()[:7]
    return result.isoformat()


def enrollment(value: object) -> str:
    name = text(value)
    return name or MISSING


def cell(sheet: Mapping[str, Any], row: int, column: int) -> object:
    r = row - sheet["start_row"]
    c = column - sheet["start_column"]
    values = sheet["values"]
    if r < 0 or r >= len(values) or c < 0 or c >= len(values[r]):
        return None
    return values[r][c]


def source_contract(profile: Mapping[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    labels = [
        value for sheet in profile["sheets"].values() for row in sheet["values"][:4]
        for value in row if isinstance(value, str)
    ]
    bases = {
        match.group(1).lower() for label in labels
        if (match := re.search(r"Based on (Amortized|Actual) Cost", label, re.IGNORECASE))
    }
    currencies = {
        match.group(1) for label in labels
        if (match := re.search(r"Currency:\s*([A-Z]{3})", label))
    }
    if len(bases) != 1:
        raise ValueError("Workbook must explicitly label exactly one cost basis")
    if currencies != {"GBP"}:
        raise ValueError("This workbook adapter requires one explicit GBP reporting currency")
    currency_label = next(label for label in labels if "Currency:" in label)
    credit_inputs = []
    sheet = profile["sheets"].get("Credits")
    if sheet and text(cell(sheet, 3, 3)) == "Credits GBP":
        for row in (4, 5):
            name, value = cell(sheet, row, 2), cell(sheet, row, 3)
            if text(name) and text(value):
                credit_inputs.append({
                    "enrollment": enrollment(name), "amount": str(units(value, f"Credits!C{row}")),
                    "source": f"Credits!C{row}",
                })
    return next(iter(bases)), currency_label, credit_inputs


def advisor_records(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    sheet = profile["sheets"].get("Advisor Data Dump")
    if sheet is None:
        return []
    headers = [text(value) for value in sheet["values"][0]]
    required = {
        "Recommendation", "Enrollment", "Sub", "Subscription Name", "Subscription ID",
        "Resource Name", "Business Impact", "Prod", "Service", "Savings GBP",
        "Potential Annual Cost Savings", "Potential Cost Savings Currency", "Updated Date",
    }
    if not required.issubset(headers):
        raise ValueError("Advisor Data Dump headers have changed; adapter needs updating")
    result = []
    for number, values in enumerate(sheet["values"][1:], sheet["start_row"] + 1):
        row = dict(zip(headers, values))
        if not any(text(value) for value in values[:14]):
            continue
        if not text(row["Recommendation"]):
            raise ValueError(f"Advisor Data Dump!{number}: missing recommendation")
        location = f"Advisor Data Dump!{number}"
        gbp = row["Savings GBP"]
        native = row["Potential Annual Cost Savings"]
        native_amount = (
            format(decimal_value(native, location, thousands=True), "f") if text(native) else None
        )
        subscription = text(row["Sub"]) or text(row["Subscription Name"]) or MISSING
        suffix = f" ({text(row['Subscription ID'])})"
        if text(row["Subscription ID"]) and subscription.endswith(suffix):
            subscription = subscription[:-len(suffix)]
        result.append({
            "row": number,
            "enrollment": enrollment(row["Enrollment"]),
            "service": text(row["Service"]) or MISSING,
            "subscription": subscription,
            "subscription_id": text(row["Subscription ID"]),
            "resource": text(row["Resource Name"]) or MISSING,
            "recommendation": text(row["Recommendation"]),
            "impact": text(row["Business Impact"]) or MISSING,
            "production": text(row["Prod"]).lower() or MISSING,
            "annual_gbp": str(units(gbp, location)) if text(gbp) else None,
            "native_annual": native_amount,
            "native_currency": text(row["Potential Cost Savings Currency"]) or MISSING,
            "updated": excel_date(row["Updated Date"], location) if text(row["Updated Date"]) else None,
        })
    return result


def calculation_inputs(profile: Mapping[str, Any]) -> tuple[dict[str, Decimal | None] | None, dict[str, Decimal | None]]:
    headers = profile.get("data_headers", [])
    formulas = profile.get("data_formulas", [])
    conversion = None
    if "Cost GBP" in headers and len(formulas) > 1:
        formula = text(formulas[1][headers.index("Cost GBP")])
        match = re.fullmatch(
            r'=\s*IF\(\s*Q(?P<row>\d+)\s*=\s*"(?P<name>[^"]+)"\s*,\s*'
            r'O(?P=row)\s*\*\s*(?P<rate>[0-9.]+)\s*,\s*O(?P=row)\s*\)',
            formula,
        )
        if match:
            rate = decimal_value(match["rate"], "Saved workbook conversion")
            if not Decimal(0) < rate < Decimal(1000000) or rate.as_tuple().exponent < -18:
                raise ValueError("Saved workbook conversion must be positive and below 1,000,000")
            for sheet in profile["sheets"].values():
                for values in sheet["values"][:4]:
                    for value in values:
                        if isinstance(value, str) and "exchange rate:" in value:
                            labelled = re.search(r"exchange rate:\s*([0-9.]+)", value)
                            if labelled and Decimal(labelled[1]) != rate:
                                raise ValueError("Saved conversion formula disagrees with the workbook currency label")
            conversion = {enrollment(match["name"]): rate}
    if "conversion_rates" in profile:
        conversion = {}
        for name, value in profile["conversion_rates"].items():
            if value is None:
                conversion[enrollment(name)] = None
                continue
            rate = decimal_value(value, f"Explicit CSV conversion for {name}")
            if not Decimal(0) < rate < Decimal(1000000) or rate.as_tuple().exponent < -18:
                raise ValueError("CSV conversion must be positive, below 1,000,000 and at most 18 places")
            conversion[enrollment(name)] = rate
    discounts: dict[str, Decimal | None] = {}
    sheet = profile["sheets"].get("Command Panel")
    if sheet and text(cell(sheet, 1, 5)) == "Product" and text(cell(sheet, 1, 6)) == "Discount":
        for number in range(2, sheet["start_row"] + len(sheet["values"])):
            product = text(cell(sheet, number, 5))
            if not product:
                continue
            try:
                discount = decimal_value(cell(sheet, number, 6), f"Command Panel!F{number}")
            except ValueError:
                discounts[product] = None
                continue
            value = discount if Decimal(0) <= discount < Decimal(1) else None
            discounts[product] = value if product not in discounts or discounts[product] == value else None
    return conversion, discounts


def savings_components(
    row: Mapping[str, object], cost: Decimal, conversion: Mapping[str, Decimal | None] | None,
    discounts: Mapping[str, Decimal | None],
) -> tuple[int, int, int, int, str | None]:
    model = MODEL_ALIASES.get(text(row["PricingModel"]), text(row["PricingModel"]))
    product = text(row.get("Product"))
    if "Commitment Tier" in product:
        discount = discounts.get(product)
        if discount is None:
            return 0, 0, 0, 1, "Missing or ambiguous source commitment-tier discount"
        with localcontext() as ctx:
            ctx.prec = 80
            return 0, units(cost * discount / (1 - discount), "Commitment-tier estimate"), 1, 0, None
    if model not in ("Reservation", "SavingsPlan"):
        return 0, 0, 0, 0, None
    if text(row["Utilization Type"]) == "Unused":
        return 0, 0, 1, 0, None
    if text(row["Utilization Type"]) != "Used":
        return 0, 0, 0, 1, "Missing benefit used/unused classification"
    if conversion is None:
        return 0, 0, 0, 1, "No supported saved GBP conversion formula for source-price estimate"
    try:
        baseline = decimal_value(row.get("Unit"), "Source Unit baseline")
        quantity = decimal_value(row.get("Quantity"), "Source quantity")
    except ValueError as exc:
        return 0, 0, 0, 1, f"Unpriced covered row: {exc}"
    if quantity == 0 and cost != 0:
        return 0, 0, 0, 1, "Non-zero covered cost with zero quantity"
    rate = conversion.get(enrollment(row["Enrolment"]), Decimal(1))
    if rate is None:
        return 0, 0, 0, 1, "No explicit currency conversion for source-price estimate"
    with localcontext() as ctx:
        ctx.prec = 80
        # Cost GBP, not Effective * Quantity, remains the authoritative observed amount.
        estimate = baseline * quantity * rate - cost
        return units(estimate, "Source-price gross estimate"), 0, 1, 0, None


def build_snapshot(
    profile: Mapping[str, Any], rows: Iterable[Mapping[str, object]],
    *, source_row_counts: bool = False,
) -> dict[str, Any]:
    basis, currency_label, credit_inputs = source_contract(profile)
    grouped: dict[tuple[str, ...], list[int]] = {}
    source_totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    normalized_totals: Counter[tuple[str, str]] = Counter()
    native_totals: Counter[tuple[str, ...]] = Counter()
    accounts: dict[str, set[str]] = defaultdict(set)
    month_cache: dict[str, str] = {}
    template_rows = 0
    source_rows = 0
    input_rows = 0
    conversion, discounts = calculation_inputs(profile)
    calculation_groups: dict[tuple[str, ...], list[int]] = {}
    native_calculation_groups: dict[tuple[str, ...], list[int]] = {}
    calculation_issues: Counter[str] = Counter()
    publisher_mismatches = 0
    required = {field for _, field in DIMENSIONS} | {"Cost", "Cost GBP", "BillingAccountId"}
    with localcontext() as ctx:
        ctx.prec = 80
        for number, row in enumerate(rows, 2):
            if not required.issubset(row):
                raise ValueError("The data sheet is missing required columns")
            if all(not text(row.get(field)) for field in SOURCE_FIELDS):
                template_rows += 1
                continue
            weight = 1
            if source_row_counts:
                raw_weight = text(row.get("SourceRowCount"))
                if not raw_weight.isdigit() or not 1 <= int(raw_weight) <= 2**53 - 1:
                    raise ValueError(f"data!{number}: invalid original usage-row count")
                weight = int(raw_weight)
            input_rows += 1
            for field in ("BillingPeriodStartDate", "BillingAccountId", "Enrolment", "Cost", "Cost GBP"):
                if not text(row[field]):
                    raise ValueError(f"data!{number}: missing {field}; row was not valued at zero")
            raw_month = text(row["BillingPeriodStartDate"])
            if raw_month not in month_cache:
                month_cache[raw_month] = excel_date(raw_month, f"data!B{number}", month=True)
            name = enrollment(row["Enrolment"])
            native = decimal_value(row["Cost"], f"data!O{number}")
            cost = decimal_value(row["Cost GBP"], f"data!P{number}")
            cost_units = units(cost, f"data!P{number}")
            # Validate the original amount too, even though only the saved GBP column is reported.
            units(native, f"data!O{number}")
            values = [text(row[field]) or MISSING for _, field in DIMENSIONS]
            values[0] = month_cache[raw_month]
            values[1] = name
            flag = text(row["IsAzureCreditEligible"]).lower()
            if flag not in ("true", "false", ""):
                raise ValueError(f"data!G{number}: invalid credit-eligibility flag")
            values[8] = {"true": "Yes", "false": "No", "": MISSING}[flag]
            publisher = text(row.get("PublisherType")) or MISSING
            if publisher == "Marketplace" and values[9] != "Market Place":
                publisher_mismatches += weight
            native_currency = text(row.get("BillingCurrency") or row.get("Currency")) or MISSING
            charge_type = text(row.get("ChargeType")) or MISSING
            key = tuple(values + [publisher, native_currency, charge_type])
            aggregate = grouped.setdefault(key, [0, 0])
            aggregate[0] += cost_units
            aggregate[1] += weight
            native_totals[key] += units(native, f"data!O{number}")
            gross, tier, priced, missing, issue = savings_components(row, cost, conversion, discounts)
            calculation = calculation_groups.setdefault(key, [0, 0, 0, 0])
            for index, value in enumerate((gross, tier, priced, missing)):
                calculation[index] += value * (weight if index >= 2 else 1)
            native_components = savings_components(row, native, {name: Decimal(1)}, discounts)[:4]
            if not re.fullmatch(r"[A-Z]{3}", native_currency):
                native_components = (0, 0, 0, 1)
            native_calculation = native_calculation_groups.setdefault(key, [0, 0, 0, 0])
            for index, value in enumerate(native_components):
                native_calculation[index] += value * (weight if index >= 2 else 1)
            if issue:
                calculation_issues[issue] += weight
            scope = (values[0], name)
            source_totals[scope] += cost
            normalized_totals[scope] += cost_units
            accounts[name].add(text(row["BillingAccountId"]).removesuffix(".0"))
            source_rows += weight
        if not source_rows:
            raise ValueError("No populated cost rows; refusing to build a zero-shaped report")
        declared_rows = profile.get("data_rows")
        if declared_rows is not None and declared_rows != input_rows + template_rows:
            raise ValueError("Extract row count does not match the workbook profile")
        reconciliations = []
        for scope, total in sorted(source_totals.items()):
            normalized = Decimal(normalized_totals[scope]) / FACTOR
            if total.quantize(CENT, rounding=ROUND_HALF_EVEN) != normalized.quantize(
                CENT, rounding=ROUND_HALF_EVEN,
            ):
                raise ValueError(f"Reporting precision changed the penny total for {scope}")
            reconciliations.append({
                "month": scope[0], "enrollment": scope[1], "source_gbp": format(total, "f"),
                "dashboard_gbp": format(normalized, "f"),
                "normalization_delta_gbp": format(normalized - total, "f"),
            })

    dimension_values = {
        name: sorted({key[index] for key in grouped}) for index, (name, _) in enumerate(DIMENSIONS)
    }
    lookup = {
        name: {value: index for index, value in enumerate(values)}
        for name, values in dimension_values.items()
    }
    packed = [
        [lookup[name][key[index]] for index, (name, _) in enumerate(DIMENSIONS)]
        + [str(value[0]), value[1]]
        for key, value in sorted(grouped.items())
    ]
    calculation_rows = [
        [str(calculation_groups[key][0]), str(calculation_groups[key][1]),
         calculation_groups[key][2], calculation_groups[key][3]]
        for key in sorted(grouped)
    ]
    native_calculation_rows = [
        [str(native_calculation_groups[key][0]), str(native_calculation_groups[key][1]),
         native_calculation_groups[key][2], native_calculation_groups[key][3]]
        for key in sorted(grouped)
    ]
    publisher_rows = [key[len(DIMENSIONS)] for key in sorted(grouped)]
    native_currencies = [key[-2] for key in sorted(grouped)]
    charge_types = [key[-1] for key in sorted(grouped)]
    native_costs = [str(native_totals[key]) if key[-2] != MISSING else None for key in sorted(grouped)]
    errors = Counter(
        (name, ERRORS[value])
        for name, sheet in profile["sheets"].items() for row in sheet["values"] for value in row
        if isinstance(value, int) and not isinstance(value, bool) and value in ERRORS
    )
    advisor = advisor_records(profile)
    dates = sorted(row["updated"] for row in advisor if row["updated"])
    missing_accounts = sorted(
        {row["enrollment"] for row in credit_inputs} - set(accounts)
    )
    warnings = []

    def warning(code: str, title: str, detail: str) -> None:
        warnings.append({"code": code, "title": title, "detail": detail})

    months = dimension_values["month"]
    filename_month = re.match(r"^(\d{4})(\d{2})\d{2}", profile["source_name"])
    if filename_month and f"{filename_month[1]}-{filename_month[2]}" not in months:
        warning("filename-period", "Filename and data period differ",
                "The filename is not the reporting period. Billing months come from populated data rows.")
    if len(months) < 2:
        warning("history", "Only one billing month is populated",
                "No month-on-month comparison, year-to-date total or annual spend forecast is reported.")
    if missing_accounts:
        warning("missing-enrollment", "Missing enrollment spend",
                f"No populated spend rows for {', '.join(missing_accounts)}. Missing spend is not zero.")
    if template_rows:
        warning("template", "Blank template rows excluded",
                f"{template_rows:,} rows have no source fields; their calculated labels and zeroes are not data.")
    if errors:
        warning("excel-errors", "Workbook summary errors excluded",
                f"{sum(errors.values()):,} Excel error cells were found in summary sheets. "
                "Spend is rebuilt from populated data rows, not summary formula results.")
    savings_detail = (
        "The Savings column is not independently validated for pricing-model classification, "
        "baseline prices or unused-benefit currency. It is excluded; no realized-savings claim is made."
    )
    formulas = profile.get("data_formulas", [])
    headers = profile.get("data_headers", [])
    if "Savings" in headers and len(formulas) > 1:
        formula = text(formulas[1][headers.index("Savings")])
        if '"On Demand"' in formula and "OnDemand" in dimension_values["pricing"]:
            savings_detail = (
                "The Savings column is excluded: its On Demand/OnDemand test is inconsistent. "
                "Its unused-benefit currency branch is not independently validated. "
                "No realized-savings claim is made."
            )
    warning("savings", "Workbook savings are not reportable", savings_detail)
    if publisher_mismatches:
        warning("publisher-classification", "Workbook usage labels misclassify Marketplace rows",
                f"{publisher_mismatches:,} Marketplace-publisher rows are not labelled Market Place by "
                "the workbook's Usage Type formula. Corrected splits use PublisherType; the original label remains in exports.")
    warning("completeness", "Period and invoice completeness not certified",
            "Billing-month records do not prove complete exports or invoice reconciliation. "
            "The cost basis is taken from the workbook label.")
    if advisor:
        missing_gbp = sum(row["annual_gbp"] is None for row in advisor)
        unmapped = sum(row["enrollment"] == MISSING for row in advisor)
        if missing_gbp or unmapped:
            warning("advisor-gaps", "Advisor mapping and valuation gaps",
                    f"{unmapped:,} recommendations lack enrollment mapping; {missing_gbp:,} lack a "
                    "workbook GBP estimate. They remain visible, not assigned or valued at zero.")
    if calculation_issues:
        warning("estimate-inputs", "Some savings-estimate inputs are unavailable",
                f"{sum(calculation_issues.values()):,} eligible rows lack a usable price, quantity, "
                "saved conversion or explicit discount. Known components are shown as partial, never a full saving.")
    result = {
        "version": 1, "scale": SCALE, "dimension_order": [name for name, _ in DIMENSIONS],
        "dimensions": dimension_values, "rows": packed, "advisor": advisor,
        "calculations": {"version": 1, "rows": calculation_rows}, "row_publishers": publisher_rows,
        "native_calculations": {"version": 1, "rows": native_calculation_rows},
        "row_native_currencies": native_currencies, "row_charge_types": charge_types, "row_native_costs": native_costs,
        "meta": {
            "source_name": profile["source_name"], "source_sha256": profile["source_sha256"],
            "source_modified_utc": profile.get("source_modified_utc"),
            "extracted_utc": profile["extracted_utc"],
            "generated_utc": datetime.now(UTC).isoformat(),
            "basis": basis, "currency": "GBP", "currency_label": currency_label,
            "source_rows": source_rows, "template_rows": template_rows,
            "input_group_rows": input_rows if source_row_counts else None,
            "grouped_rows": len(packed), "missing_spend_enrollments": missing_accounts,
            "billing_accounts": {key: sorted(value) for key, value in sorted(accounts.items())},
            "credit_inputs": credit_inputs,
            "advisor_date_start": dates[0] if dates else None,
            "advisor_date_end": dates[-1] if dates else None,
            "calculation_issues": [{"reason": reason, "rows": count}
                                   for reason, count in sorted(calculation_issues.items())],
            "discount_inputs": [
                {"product": product, "discount": format(value, "f") if value is not None else None}
                for product, value in sorted(discounts.items())
            ],
            "publisher_mismatch_rows": publisher_mismatches,
            "commitment_expiry_note": text(cell(profile["sheets"]["Savings and Utilization Rate"], 52, 1))
            if "Savings and Utilization Rate" in profile["sheets"] else "",
        },
        "audit": {
            "reconciliations": reconciliations,
            "excel_errors": [
                {"sheet": sheet, "error": error, "count": count}
                for (sheet, error), count in sorted(errors.items())
            ],
            "warnings": warnings,
        },
    }
    source_meta = dict(result["meta"])
    source_meta["available_scopes"] = [
        {"month": month, "enrollment": name} for month, name in sorted(source_totals)
    ]
    result["meta"]["sources"] = [source_meta]
    result["row_sources"] = [0] * len(packed)
    result["advisor_snapshots"] = [{"source": 0, "rows": advisor}]
    return result


def combine_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    if not snapshots:
        raise ValueError("At least one source workbook is required")
    if len(snapshots) == 1:
        return snapshots[0]
    if len({(s["meta"]["basis"], s["meta"]["currency"]) for s in snapshots}) != 1:
        raise ValueError("Source workbooks must use the same cost basis and reporting currency")
    sources = []
    seen_hashes: set[str] = set()
    seen_scopes: set[tuple[str, str]] = set()
    for snapshot in snapshots:
        source = snapshot["meta"]
        if len(source.get("sources", [])) != 1 or "calculations" not in snapshot:
            raise ValueError("Combine freshly built original sources, not an existing bundle or unpriced snapshot")
        if source["source_sha256"] in seen_hashes:
            raise ValueError("Duplicate workbook: refusing to double count the same source")
        seen_hashes.add(source["source_sha256"])
        for scope in source["sources"][0]["available_scopes"]:
            accounts = source["billing_accounts"][scope["enrollment"]]
            if len(accounts) != 1:
                raise ValueError("Enrollment identity is ambiguous across source workbooks")
            identity = (scope["month"], accounts[0])
            if identity in seen_scopes:
                raise ValueError(f"Overlapping source scope {identity}; select one workbook instead of double counting")
            seen_scopes.add(identity)
        sources.append(source["sources"][0])
    dimensions = {
        name: sorted({value for s in snapshots for value in s["dimensions"][name]})
        for name, _ in DIMENSIONS
    }
    lookup = {name: {value: index for index, value in enumerate(values)} for name, values in dimensions.items()}
    rows = []
    row_sources = []
    calculations = []
    publishers = []
    native_currencies = []
    charge_types = []
    native_costs = []
    native_calculations = []
    for source_index, snapshot in enumerate(snapshots):
        source_native_currencies = snapshot.get("row_native_currencies", [MISSING] * len(snapshot["rows"]))
        source_charge_types = snapshot.get("row_charge_types", [MISSING] * len(snapshot["rows"]))
        source_native_costs = snapshot.get("row_native_costs", [None] * len(snapshot["rows"]))
        source_native_calculations = snapshot.get("native_calculations", {}).get(
            "rows", [None] * len(snapshot["rows"]),
        )
        for index, row in enumerate(snapshot["rows"]):
            ids = [
                lookup[name][snapshot["dimensions"][name][row[column]]]
                for column, (name, _) in enumerate(DIMENSIONS)
            ]
            rows.append(ids + row[len(DIMENSIONS):])
            row_sources.append(source_index)
            calculations.append(snapshot["calculations"]["rows"][index])
            publishers.append(snapshot["row_publishers"][index])
            native_currencies.append(source_native_currencies[index])
            charge_types.append(source_charge_types[index])
            native_costs.append(source_native_costs[index])
            native_calculations.append(source_native_calculations[index])
    latest_advisor = max(
        range(len(sources)), key=lambda index: sources[index]["advisor_date_end"] or "",
    )
    credits = []
    credit_seen = set()
    accounts: dict[str, set[str]] = defaultdict(set)
    for source in sources:
        for name, ids in source["billing_accounts"].items():
            accounts[name].update(ids)
        for credit in source["credit_inputs"]:
            identity = credit["enrollment"], credit["amount"]
            if identity not in credit_seen:
                credit_seen.add(identity)
                credits.append({**credit, "source_name": source["source_name"]})
    for name, ids in accounts.items():
        if len(ids) != 1:
            raise ValueError(f"Conflicting source enrollment identities for {name}")
    labels = sorted({source["currency_label"] for source in sources})
    meta = {
        **snapshots[0]["meta"],
        "source_name": f"{len(sources)} supplied workbooks",
        "source_sha256": hashlib.sha256("|".join(source["source_sha256"] for source in sources).encode()).hexdigest(),
        "source_modified_utc": None,
        "extracted_utc": max(source["extracted_utc"] for source in sources),
        "generated_utc": datetime.now(UTC).isoformat(),
        "source_rows": sum(source["source_rows"] for source in sources),
        "template_rows": sum(source["template_rows"] for source in sources),
        "grouped_rows": len(rows),
        "credit_inputs": credits, "sources": sources,
        "currency_label": labels[0] if len(labels) == 1 else "GBP at each source workbook's saved conversion; rates vary by source",
        "billing_accounts": {name: sorted(ids) for name, ids in sorted(accounts.items())},
        "missing_spend_enrollments": sorted({row["enrollment"] for row in credits} - set(accounts)),
        "advisor_date_start": sources[latest_advisor]["advisor_date_start"],
        "advisor_date_end": sources[latest_advisor]["advisor_date_end"],
        "calculation_issues": [row for source in sources for row in source["calculation_issues"]],
        "discount_inputs": [row for source in sources for row in source["discount_inputs"]],
        "publisher_mismatch_rows": sum(source["publisher_mismatch_rows"] for source in sources),
    }
    return {
        "version": 1, "scale": SCALE, "dimension_order": [name for name, _ in DIMENSIONS],
        "dimensions": dimensions, "rows": rows, "row_sources": row_sources, "row_publishers": publishers, "meta": meta,
        "row_native_currencies": native_currencies, "row_charge_types": charge_types, "row_native_costs": native_costs,
        "calculations": {"version": 1, "rows": calculations},
        "native_calculations": {"version": 1, "rows": native_calculations},
        "advisor": snapshots[latest_advisor]["advisor"],
        "advisor_snapshots": [{"source": index, "rows": snapshot["advisor"]} for index, snapshot in enumerate(snapshots)],
        "audit": {
            name: [{**row, "source_name": snapshot["meta"]["source_name"]} for snapshot in snapshots for row in snapshot["audit"][name]]
            for name in ("warnings", "excel_errors", "reconciliations")
        },
    }


def extract_workbook(source: Path, target: Path) -> None:
    try:
        import win32com.client
    except ImportError as exc:
        raise RuntimeError(
            "Workbook refresh requires Windows desktop Excel and pywin32 in this Python environment. "
            "Use the Python installation with pywin32, or install reporting\\mbr\\requirements.txt."
        ) from exc

    with source.open("rb") as handle:
        before = hashlib.file_digest(handle, "sha256").hexdigest()
    excel = win32com.client.DispatchEx("Excel.Application")
    workbook = None
    try:
        excel.AutomationSecurity = 3
        excel.EnableEvents = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        excel.Visible = False
        workbook = excel.Workbooks.Open(
            str(source), UpdateLinks=0, ReadOnly=True, Password="", WriteResPassword="",
            IgnoreReadOnlyRecommended=True, Notify=False, AddToMru=False,
        )
        profile: dict[str, Any] = {
            "source_name": source.name, "source_sha256": before,
            "source_modified_utc": datetime.fromtimestamp(
                source.stat().st_mtime, UTC,
            ).isoformat(),
            "extracted_utc": datetime.now(UTC).isoformat(), "sheets": {},
        }
        for sheet in workbook.Worksheets:
            used = sheet.UsedRange
            if sheet.Name != "data":
                profile["sheets"][sheet.Name] = {
                    "range": used.Address, "start_row": used.Row, "start_column": used.Column,
                    "values": used.Value2, "formulas": used.Formula,
                }
                continue
            last_row = used.Row + used.Rows.Count - 1
            last_column = used.Column + used.Columns.Count - 1
            header = sheet.Range(sheet.Cells(1, 1), sheet.Cells(1, last_column)).Value2[0]
            profile["data_rows"] = last_row - 1
            profile["data_headers"] = header
            profile["data_formulas"] = sheet.Range(
                sheet.Cells(1, 1), sheet.Cells(min(3, last_row), last_column),
            ).Formula
            with (target / "mbr-data.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                for first in range(2, last_row + 1, 10000):
                    last = min(first + 9999, last_row)
                    block = sheet.Range(sheet.Cells(first, 1), sheet.Cells(last, last_column)).Value2
                    writer.writerows(
                        [ERRORS.get(value, value) if isinstance(value, int) else value for value in row]
                        for row in block
                    )
                    print(f"Read {last - 1:,} of {last_row - 1:,} cost-sheet rows", flush=True)
        if "data_headers" not in profile:
            raise ValueError("Workbook has no data sheet")
    finally:
        try:
            if workbook is not None:
                workbook.Close(SaveChanges=False)
        finally:
            excel.Quit()
    with source.open("rb") as handle:
        after = hashlib.file_digest(handle, "sha256").hexdigest()
    if before != after:
        raise RuntimeError("The source workbook changed during extraction; refresh was not published")
    (target / "mbr-profile.json").write_text(json.dumps(profile), encoding="utf-8")


def parse_dashboard(content: str) -> dict[str, Any]:
    matches = re.findall(
        r'<script type="application/json" id="mbr-data">([\s\S]*?)</script>',
        content,
    )
    if len(matches) != 1:
        raise ValueError("Expected one embedded reporting snapshot")
    return json.loads(matches[0])


def read_dashboard(path: Path) -> dict[str, Any]:
    return parse_dashboard(path.read_text(encoding="utf-8"))


def render_html(snapshot: Mapping[str, Any], host: Mapping[str, Any] | None = None) -> str:
    template = (ASSETS / "dashboard.html").read_text(encoding="utf-8")
    script = (ASSETS / "dashboard.js").read_text(encoding="utf-8")
    if any(template.count(marker) != 1 for marker in ("__MBR_DATA__", "__MBR_SCRIPT__", "__MBR_HOST__")):
        raise ValueError("Dashboard template placeholders are invalid")
    def safe_json(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        for character, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
            payload = payload.replace(character, escaped)
        return payload
    if host:
        template = template.replace("connect-src 'none'", "connect-src 'self'")
    return template.replace("__MBR_SCRIPT__", script).replace(
        "__MBR_DATA__", safe_json(snapshot),
    ).replace("__MBR_HOST__", safe_json(host or {"available": False}))


def write_atomic(output: Path, content: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def read_extraction(extraction: Path) -> dict[str, Any]:
    profile = json.loads(
        (extraction / "mbr-profile.json").read_text(encoding="utf-8"), parse_float=Decimal,
    )
    with (extraction / "mbr-data.csv").open(encoding="utf-8", newline="") as handle:
        return build_snapshot(profile, csv.DictReader(handle))


def publish(extraction: Path, output: Path) -> dict[str, Any]:
    snapshot = read_extraction(extraction)
    write_atomic(output, render_html(snapshot))
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", nargs="?", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "mbr" / "Azure-MBR-Dashboard.html")
    parser.add_argument("--from-extract", type=Path, help="Reuse an existing local extraction")
    parser.add_argument("--include-source", type=Path, action="append", default=[],
                        help="Add a workbook with non-overlapping month/enrollment scopes")
    parser.add_argument("--include-extract", type=Path, action="append", default=[],
                        help="Add a previously extracted non-overlapping source")
    args = parser.parse_args()
    if bool(args.workbook) == bool(args.from_extract):
        parser.error("Provide either a workbook or --from-extract, not both")
    output = args.output.resolve()
    if output.suffix.lower() != ".html":
        parser.error("Output must be an .html file")
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshots = []
    if args.from_extract:
        snapshots.append(read_extraction(args.from_extract.resolve()))
    source_paths = ([args.workbook] if args.workbook else []) + args.include_source
    for path in source_paths:
        source = path.resolve(strict=True)
        if source == output:
            parser.error("Output cannot replace a source workbook")
        with tempfile.TemporaryDirectory(prefix=".mbr-extract-", dir=output.parent) as directory:
            extraction = Path(directory)
            extract_workbook(source, extraction)
            snapshots.append(read_extraction(extraction))
    snapshots.extend(read_extraction(path.resolve()) for path in args.include_extract)
    snapshot = combine_snapshots(snapshots)
    write_atomic(output, render_html(snapshot))
    print(f"Dashboard: {output}")
    print(
        f"{snapshot['meta']['source_rows']:,} populated rows; "
        f"{snapshot['meta']['template_rows']:,} blank template rows excluded; "
        f"{len(snapshot['advisor']):,} Advisor recommendations"
    )
    for reconciliation in snapshot["audit"]["reconciliations"]:
        print(
            f"{reconciliation['month']} {reconciliation['enrollment']}: "
            f"GBP {reconciliation['dashboard_gbp']} (reconciled to source to the penny)"
        )


if __name__ == "__main__":
    main()
