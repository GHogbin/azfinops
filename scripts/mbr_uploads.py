"""Validated annual history and two-month comparison state; no main-database access."""
from __future__ import annotations

import csv
import hashlib
import re
import sys
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import build_mbr_dashboard as mbr

sys.path.insert(0, str(mbr.ROOT / "src"))
from azfinops.fx import CCY, MONTH, decimal_rate, rates_to, resolve
from azfinops.ingestion.mapping import ALIASES, FOCUS_MARKERS

MONTH_LIMIT = 2
MAX_UPLOAD_BYTES = 128 * 1024 * 1024
REPORT_COLUMNS = {
    "BillingPeriodStartDate": ("billingperiodstartdate", *ALIASES["usage_date"], "usage_date"),
    "BillingAccountId": ALIASES["billing_account_id"],
    "Enrolment": ("enrolment", "enrollment", *ALIASES["billing_account_name"]),
    "MeterCategory": ALIASES["meter_category"],
    "Product": ("product", "productname"),
    "SubscriptionName": ("subscriptionname",),
    "IsAzureCreditEligible": ("isazurecrediteligible",),
    "ProductOrderName": ("productordername",),
    "PublisherType": ALIASES["publisher_type"],
    "PricingModel": ALIASES["pricing_model"],
    "ResourceLocation": ALIASES["region"],
    "Effective": ("effective", *ALIASES["effective_price"]),
    "Unit": ("unit", *ALIASES["unit_price"]),
    "Quantity": ALIASES["quantity"],
    "Cost": ALIASES["cost"],
    "Cost GBP": ("cost gbp",),
    "Currency": ("currency", *ALIASES["billing_currency"]),
    "Usage Type": ("usage type",),
    "Utilization Type": ("utilization type",),
    "Sub Type": ("sub type",),
    "ChargeType": ALIASES["charge_type"],
}
REQUIRED_COST = (
    "BillingPeriodStartDate", "BillingAccountId", "MeterCategory", "SubscriptionName",
    "PricingModel", "PublisherType", "Cost",
)
ADVISOR_COLUMNS = (
    "Source.Name", "Business Impact", "Recommendation", "Subscription ID", "Subscription Name",
    "Resource Group", "Resource Name", "Type", "Updated Date", "Potential Annual Cost Savings",
    "Potential Cost Savings Currency", "Potential carbon reductions", "Cost implications (Preview)",
    "Description of changes", "Column1", "Sub", "Enrollment", "Prod", "Savings GBP", "Service",
)
REQUIRED_ADVISOR = (
    "Recommendation", "Subscription ID", "Subscription Name", "Resource Name", "Business Impact",
    "Updated Date", "Potential Annual Cost Savings", "Potential Cost Savings Currency",
)
UPLOAD_SCHEMA = {
    "cost_required": [
        {"name": name, "accepted": sorted({name, *REPORT_COLUMNS[name]})}
        for name in REQUIRED_COST
    ],
    "cost_currency": "BillingCurrency (or BillingCurrencyCode/Currency), or an explicit currency selection. "
    "Non-GBP costs need Cost GBP or a user-supplied GBP-per-unit rate; no guessed FX.",
    "cost_optional": ["Enrolment", "IsAzureCreditEligible", "ResourceLocation", "Sub Type",
                      "Utilization Type or ChargeType", "Product", "UnitPrice (numeric Unit in the workbook)",
                      "Quantity", "Cost GBP"],
    "advisor_required": list(REQUIRED_ADVISOR),
    "advisor_optional": ["Enrollment", "Prod", "Service", "Savings GBP", "Sub"],
    "workbook": "The original .xlsb/.xlsx MBR layout: data, labelled cost basis and GBP currency; "
    "Advisor Data Dump, Credits and Command Panel are read when present. Desktop Excel must open it "
    "with the signed-in user's existing access. Macros and link updates remain disabled.",
    "retention": "Detailed comparisons use the two newest supplied billing months shared across enrollments. "
    "Separate grouped annual history supports YTD, credit/savings matrices and projections; older uploads "
    "backfill that history without changing the comparison window. Missing periods are not filled. "
    "Advisor keeps two dated snapshots. Source files and the main database are unchanged.",
    "upload_limit_bytes": MAX_UPLOAD_BYTES,
}


def _metadata(filename: str, digest: str, basis: str) -> dict[str, Any]:
    if basis not in ("amortized", "actual"):
        raise ValueError("Declare the cost basis as amortized or actual")
    now = datetime.now(UTC).isoformat()
    return {
        "source_name": filename, "source_sha256": digest, "source_modified_utc": None,
        "extracted_utc": now,
        "sheets": {"CSV declaration": {
            "start_row": 1, "start_column": 1,
            "values": [[f"Based on {basis.title()} Cost"], ["Currency: GBP"]],
        }},
    }


def _header_map(headers: list[str] | None, fields: Mapping[str, tuple[str, ...]]) -> dict[str, str | None]:
    if not headers:
        raise ValueError("CSV has no header row")
    cleaned = [header.lstrip("\ufeff").strip().casefold() for header in headers]
    if any(not header for header in cleaned) or len(cleaned) != len(set(cleaned)):
        raise ValueError("CSV headers must be non-empty and unique ignoring case/spacing")
    if FOCUS_MARKERS & set(cleaned):
        raise ValueError("FOCUS is not supported. Use Azure Cost and usage details or the MBR data CSV.")
    result = {}
    for name, aliases in fields.items():
        hits = [header for header, lower in zip(headers, cleaned) if lower in {name.casefold(), *aliases}]
        if len(hits) > 1:
            raise ValueError(f"Ambiguous {name} columns: {', '.join(hits)}; retain one authoritative column")
        result[name] = hits[0] if hits else None
    return result


def _required(mapping: Mapping[str, str | None], names: tuple[str, ...]) -> None:
    missing = [name for name in names if mapping[name] is None]
    if missing:
        raise ValueError("Missing required column(s): " + ", ".join(missing))


def _csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        declaration = handle.readline().strip().casefold()
        declared_comma = declaration in {"sep=,", '"sep=,"'}
        if not declared_comma:
            handle.seek(0)
        reader = csv.DictReader(handle, strict=True)
        headers = reader.fieldnames or []
        rows = []
        for number, row in enumerate(reader, 3 if declared_comma else 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV row {number}: field count differs from the header")
            if any(value.strip() for value in row.values()):
                rows.append(row)
    if not rows:
        raise ValueError("CSV contains no populated records")
    return headers, rows


def _billing_month(value: str, location: str, from_period: bool) -> str:
    raw = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        raw += "-01"
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", raw):
        raw = datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
    if not from_period:
        raw = mbr.excel_date(raw, location)[:7] + "-01"
    return mbr.excel_date(raw, location, month=True) + "-01"


def cost_csv(
    path: Path, filename: str, digest: str, current: Mapping[str, Any],
    basis: str, currency_input: str = "", rate_input: str = "",
) -> dict[str, Any]:
    headers, values = _csv_rows(path)
    mapping = _header_map(headers, REPORT_COLUMNS)
    _required(mapping, REQUIRED_COST)
    profile = _metadata(filename, digest, basis)
    known_names: dict[str, str] = {}
    for source in current["meta"].get("sources", [current["meta"]]):
        for name, ids in source["billing_accounts"].items():
            for account in ids:
                if account in known_names and known_names[account] != name:
                    raise ValueError("Existing report has conflicting enrollment identities")
                known_names[account] = name
    rate = None
    if rate_input:
        rate = mbr.decimal_value(rate_input, "GBP-per-unit exchange rate")
        if not Decimal(0) < rate < Decimal(1000000) or rate.as_tuple().exponent < -18:
            raise ValueError("Exchange rate must be positive, below 1,000,000 and at most 18 decimal places")
    if currency_input and not re.fullmatch(r"[A-Z]{3}", currency_input):
        raise ValueError("Currency selection must be a three-letter code")
    rows = []
    rates: dict[str, str | None] = {}
    currencies = set()
    account_currencies = {}
    explicit_rate_used = False
    for number, value in enumerate(values, 2):
        row = {name: mbr.text(value.get(header)) if header else "" for name, header in mapping.items()}
        for name in REQUIRED_COST:
            if not row[name]:
                raise ValueError(f"CSV row {number}: missing {name}")
        account = row["BillingAccountId"].removesuffix(".0")
        source_name = mbr.enrollment(row["Enrolment"]) if row["Enrolment"] else None
        account_name_header = mapping["Enrolment"] and mapping["Enrolment"].casefold() == "billingaccountname"
        name = known_names.get(account, source_name) if account_name_header else source_name
        name = name or known_names.get(account, f"Enrollment {account}")
        if account in known_names and name != known_names[account]:
            raise ValueError(f"CSV row {number}: enrollment name differs from the existing account; use {known_names[account]}")
        row["Enrolment"] = name
        currency = row["Currency"].upper() or currency_input
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError(f"CSV row {number}: BillingCurrency is required, or select the file's currency")
        if currency_input and row["Currency"] and currency != currency_input:
            raise ValueError(f"CSV row {number}: BillingCurrency conflicts with the selected currency")
        currencies.add(currency)
        row["Currency"] = currency
        if account in account_currencies and account_currencies[account] != currency:
            raise ValueError(f"CSV row {number}: one enrollment cannot mix native currencies in a report upload")
        account_currencies[account] = currency
        native = mbr.decimal_value(row["Cost"], f"CSV row {number} Cost")
        row["BillingPeriodStartDate"] = _billing_month(
            row["BillingPeriodStartDate"], f"CSV row {number} billing date",
            mapping["BillingPeriodStartDate"].strip().casefold() == "billingperiodstartdate",
        )
        if row["Cost GBP"]:
            mbr.decimal_value(row["Cost GBP"], f"CSV row {number} Cost GBP")
            if currency == "GBP":
                if mbr.units(native, "GBP Cost") != mbr.units(row["Cost GBP"], "Cost GBP"):
                    raise ValueError(f"CSV row {number}: native GBP Cost and Cost GBP disagree")
                rates[name] = "1"
            elif rate is not None:
                with localcontext() as ctx:
                    ctx.prec = 80
                    if (native * rate).quantize(mbr.CENT) != Decimal(row["Cost GBP"]).quantize(mbr.CENT):
                        raise ValueError(f"CSV row {number}: Cost GBP disagrees with the explicit exchange rate to the penny")
                rates[name] = rate_input
            else:
                rates[name] = None
        else:
            if currency != "GBP" and rate is None:
                raise ValueError(f"CSV row {number}: {currency} needs Cost GBP or an explicit GBP-per-{currency} rate")
            conversion = Decimal(1) if currency == "GBP" else rate
            with localcontext() as ctx:
                ctx.prec = 80
                row["Cost GBP"] = mbr.amount_string(mbr.units(native * conversion, "Converted CSV cost"))
            rates[name] = format(conversion, "f")
            explicit_rate_used |= currency != "GBP"
        model = mbr.MODEL_ALIASES.get(row["PricingModel"], row["PricingModel"])
        if not row["Utilization Type"]:
            if row["ChargeType"] in ("UnusedReservation", "UnusedSavingsPlan"):
                row["Utilization Type"] = "Unused"
            elif row["ChargeType"] == "Usage" and model in ("Reservation", "SavingsPlan"):
                row["Utilization Type"] = "Used"
        row["Usage Type"] = row["Usage Type"] or ("Market Place" if row["PublisherType"] == "Marketplace" else "Azure")
        row["Source.Name"] = filename
        rows.append(row)
    if len(currencies - {"GBP"}) > 1:
        raise ValueError("Split different foreign currencies into separate uploads; one rate cannot value several currencies")
    profile["conversion_rates"] = rates
    profile["data_rows"] = len(rows)
    snapshot = mbr.build_snapshot(profile, rows)
    detail = (
        f"CSV source currency: {', '.join(sorted(currencies))}. "
        + (f"Explicit GBP-per-foreign-unit rate: {rate_input}. " if rate_input else "")
        + ("Cost GBP computed from the explicit rate; no rate lookup." if explicit_rate_used
           else "Saved Cost GBP/native GBP is authoritative; no rate lookup.")
    )
    snapshot["meta"]["currency_label"] = "Currency: GBP | " + detail
    snapshot["meta"]["sources"][0]["currency_label"] = snapshot["meta"]["currency_label"]
    return snapshot


def advisor_csv(
    path: Path, filename: str, digest: str, current: Mapping[str, Any], basis: str,
) -> dict[str, Any]:
    headers, values = _csv_rows(path)
    fields = {name: (name.casefold(),) for name in ADVISOR_COLUMNS}
    fields["Updated Date"] = ("updated date", "last refreshed on")
    mapping = _header_map(headers, fields)
    _required(mapping, REQUIRED_ADVISOR)
    profile = _metadata(filename, digest, basis)
    rows = []
    for number, value in enumerate(values, 2):
        row = [value[mapping[name]] if mapping[name] else None for name in ADVISOR_COLUMNS]
        for name in ("Recommendation", "Subscription ID", "Updated Date"):
            if not mbr.text(row[ADVISOR_COLUMNS.index(name)]):
                raise ValueError(f"Advisor row {number}: missing {name}")
        if mbr.text(row[ADVISOR_COLUMNS.index("Potential Annual Cost Savings")]) and not re.fullmatch(
            r"[A-Z]{3}", mbr.text(row[ADVISOR_COLUMNS.index("Potential Cost Savings Currency")]),
        ):
            raise ValueError(f"Advisor row {number}: a populated annual estimate requires a three-letter currency")
        rows.append(row)
    profile["sheets"]["Advisor Data Dump"] = {
        "start_row": 1, "start_column": 1, "values": [list(ADVISOR_COLUMNS), *rows],
    }
    recommendations = mbr.advisor_records(profile)
    unmapped = sum(row["enrollment"] == mbr.MISSING for row in recommendations)
    missing_production = sum(row["production"] == mbr.MISSING for row in recommendations)
    missing_service = sum(row["service"] == mbr.MISSING for row in recommendations)
    unvalued = sum(row["native_annual"] is None and row["annual_gbp"] is None for row in recommendations)
    dates = sorted(row["updated"] for row in recommendations if row["updated"])
    now = datetime.now(UTC).isoformat()
    source = {
        "source_name": filename, "source_sha256": digest, "source_modified_utc": None,
        "extracted_utc": now, "generated_utc": now, "basis": basis, "currency": "GBP",
        "currency_label": "Currency: GBP | Advisor source estimates; missing conversions are not invented",
        "source_rows": 0, "template_rows": 0, "grouped_rows": 0,
        "missing_spend_enrollments": [], "billing_accounts": {}, "credit_inputs": [],
        "advisor_date_start": dates[0], "advisor_date_end": dates[-1],
        "calculation_issues": [], "discount_inputs": [], "publisher_mismatch_rows": 0,
        "commitment_expiry_note": "", "available_scopes": [],
    }
    return {
        "version": 1, "scale": mbr.SCALE, "dimension_order": [name for name, _ in mbr.DIMENSIONS],
        "dimensions": {name: [] for name, _ in mbr.DIMENSIONS}, "rows": [], "row_sources": [],
        "row_publishers": [], "calculations": {"version": 1, "rows": []},
        "meta": {**source, "sources": [source]}, "advisor": recommendations,
        "advisor_snapshots": [{"source": 0, "rows": recommendations}],
        "audit": {"reconciliations": [], "excel_errors": [], "warnings": [{
            "code": "advisor-source", "title": "Advisor estimates are separate from cost",
            "detail": f"{len(recommendations):,} recommendations; {unmapped:,} lack enrollment mapping, "
            f"{missing_production:,} lack production mapping, {missing_service:,} lack service mapping, "
            f"and {unvalued:,} have no supplied annual monetary estimate. Missing values are unavailable, "
            "never zero. Unassigned records remain visible in Combined Advisor detail; production-only "
            "potential totals require the relevant mappings. Annual estimates may overlap.",
        }]},
    }


def source_scopes(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], tuple[str, int, int]]:
    sources = snapshot["meta"].get("sources", [snapshot["meta"]])
    row_sources = snapshot.get("row_sources", [0] * len(snapshot["rows"]))
    result = {}
    for index, row in enumerate(snapshot["rows"]):
        source_id = row_sources[index]
        month = snapshot["dimensions"]["month"][row[0]]
        name = snapshot["dimensions"]["enrollment"][row[1]]
        ids = sources[source_id]["billing_accounts"].get(name, [])
        if len(ids) != 1:
            raise ValueError("Report enrollment identity is missing or ambiguous")
        identity = month, ids[0]
        old = result.get(identity, (name, 0, 0))
        if old[0] != name:
            raise ValueError("Conflicting enrollment names within a scope")
        result[identity] = name, old[1] + int(row[10]), old[2] + row[11]
    return result


def _fragment(
    snapshot: Mapping[str, Any], source_id: int,
    keep: Callable[[str, str], bool], advisor: bool,
) -> dict[str, Any] | None:
    original = snapshot["meta"].get("sources", [snapshot["meta"]])[source_id]
    row_sources = snapshot.get("row_sources", [0] * len(snapshot["rows"]))
    selected = []
    for index, row in enumerate(snapshot["rows"]):
        if row_sources[index] == source_id:
            month = snapshot["dimensions"]["month"][row[0]]
            name = snapshot["dimensions"]["enrollment"][row[1]]
            account = original["billing_accounts"][name]
            if len(account) != 1:
                raise ValueError("Cannot retain an ambiguous enrollment identity")
            if keep(month, account[0]):
                selected.append(index)
    recommendations = next(
        (item["rows"] for item in snapshot.get("advisor_snapshots", [{"source": 0, "rows": snapshot.get("advisor", [])}])
         if item["source"] == source_id), [],
    ) if advisor else []
    if not selected and not recommendations:
        return None
    dimensions = {
        name: sorted({snapshot["dimensions"][name][snapshot["rows"][index][column]] for index in selected})
        for column, (name, _field) in enumerate(mbr.DIMENSIONS)
    }
    lookup = {name: {value: index for index, value in enumerate(values)} for name, values in dimensions.items()}
    packed = [
        [lookup[name][snapshot["dimensions"][name][snapshot["rows"][index][column]]]
         for column, (name, _field) in enumerate(mbr.DIMENSIONS)] + snapshot["rows"][index][10:]
        for index in selected
    ]
    scopes = {
        (dimensions["month"][row[0]], dimensions["enrollment"][row[1]]) for row in packed
    }
    source = deepcopy(original)
    source["original_source_rows"] = source.get("original_source_rows", source["source_rows"])
    source["source_rows"] = sum(row[11] for row in packed)
    source["template_rows"] = 0
    source["grouped_rows"] = len(packed)
    source["available_scopes"] = [{"month": month, "enrollment": name} for month, name in sorted(scopes)]
    source["billing_accounts"] = {name: original["billing_accounts"][name] for name in dimensions["enrollment"]}
    source["missing_spend_enrollments"] = sorted({row["enrollment"] for row in source["credit_inputs"]} - set(dimensions["enrollment"]))
    dates = sorted(row["updated"] for row in recommendations if row["updated"])
    source["advisor_date_start"], source["advisor_date_end"] = (dates[0], dates[-1]) if dates else (None, None)
    calculations = [snapshot["calculations"]["rows"][index] for index in selected]
    original_native_calculations = snapshot.get("native_calculations", {}).get(
        "rows", [None] * len(snapshot["rows"]),
    )
    missing = sum(row[3] for row in calculations)
    source["calculation_issues"] = [{"reason": "Incomplete savings inputs in retained rows", "rows": missing}] if missing else []
    original_publishers = snapshot.get("row_publishers", [mbr.MISSING] * len(snapshot["rows"]))
    publishers = [original_publishers[index] for index in selected]
    additional_rows = {
        name: [snapshot[name][index] for index in selected]
        for name in ("row_native_currencies", "row_charge_types", "row_native_costs")
        if name in snapshot
    }
    source["publisher_mismatch_rows"] = sum(
        row[11] for row, publisher in zip(packed, publishers)
        if publisher == "Marketplace" and dimensions["usage"][row[9]] != "Market Place"
    )
    def belongs(row: Mapping[str, Any]) -> bool:
        return row.get("source_name", original["source_name"]) == original["source_name"]
    audit = {
        "reconciliations": [deepcopy(row) for row in snapshot["audit"]["reconciliations"]
                           if belongs(row) and (row["month"], row["enrollment"]) in scopes],
        "excel_errors": [deepcopy(row) for row in snapshot["audit"]["excel_errors"] if belongs(row)],
        "warnings": [deepcopy(row) for row in snapshot["audit"]["warnings"] if belongs(row)
                     and row["code"] not in {"history", "filename-period", "template", "missing-enrollment", "estimate-inputs", "publisher-classification", "retention"}],
    }
    return {
        "version": 1, "scale": mbr.SCALE, "dimension_order": [name for name, _ in mbr.DIMENSIONS],
        "dimensions": dimensions, "rows": packed, "row_sources": [0] * len(packed),
        "row_publishers": publishers, "calculations": {"version": 1, "rows": calculations},
        "native_calculations": {
            "version": 1,
            "rows": [original_native_calculations[index] for index in selected],
        },
        **additional_rows,
        "meta": {**source, "sources": [source]}, "advisor": recommendations,
        "advisor_snapshots": [{"source": 0, "rows": recommendations}], "audit": audit,
    }


def usd_fx_evidence(snapshot: Mapping[str, Any], rates: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(rates, list):
        raise TypeError("Saved reporting FX must be a list of dated rate records")
    for row in rates:
        if not isinstance(row, dict) or not isinstance(row.get("rate"), str):
            raise TypeError("Each saved reporting FX record needs a decimal-string rate")
        if not all(isinstance(row.get(name), str) and CCY.fullmatch(row[name]) for name in ("from", "to")):
            raise ValueError("Saved reporting FX currencies must be three-letter ISO codes")
        if row["from"] == row["to"] or not isinstance(row.get("month"), str) or not MONTH.fullmatch(row["month"]):
            raise ValueError("Saved reporting FX needs distinct currencies and a YYYY-MM month")
        if any(name in row and not isinstance(row[name], str) for name in ("source", "method")):
            raise ValueError("Saved reporting FX source/method must be text when supplied")
        decimal_rate(row["rate"])
    needed = set()
    for value in (snapshot, snapshot.get("annual_history")):
        if value is None:
            continue
        currencies = value.get("row_native_currencies", [mbr.MISSING] * len(value["rows"]))
        for packed, currency in zip(value["rows"], currencies):
            if CCY.fullmatch(currency):
                needed.add((currency, value["dimensions"]["month"][packed[0]]))
    for source in snapshot["meta"].get("sources", [snapshot["meta"]]):
        for credit in source.get("credit_inputs", []):
            currency = credit.get("currency", "GBP")
            if CCY.fullmatch(currency):
                needed.update((currency, month) for month in snapshot["dimensions"]["month"])
    for advisor in snapshot.get("advisor_snapshots", []):
        for row in advisor["rows"]:
            month = (row.get("updated") or "")[:7]
            if not MONTH.fullmatch(month):
                continue
            currency = row.get("native_currency", mbr.MISSING)
            if CCY.fullmatch(currency):
                needed.add((currency, month))
            if row.get("annual_gbp") is not None:
                needed.add(("GBP", month))
    chosen = resolve("USD", rates, sorted(needed))
    available = {(row["from"], row["month"]): row for row in rates_to("USD", rates)}
    rows = []
    missing = []
    for currency, month in sorted(needed):
        if currency == "USD":
            continue
        selection = chosen.get((currency, month))
        if selection is None:
            missing.append({"from": currency, "month": month, "to": "USD"})
            continue
        provenance = available[currency, selection["rate_month"]]
        if provenance.get("inverted"):
            original = next(row for row in rates if row["from"] == "USD" and row["to"] == currency
                            and row["month"] == selection["rate_month"])
            provenance = {**original, "inverted": True,
                          "source": "Inverse of " + original.get("source", "saved USD-to-" + currency + " rate")}
        rows.append({
            "from": currency, "month": month, "to": "USD",
            "rate": format(selection["rate"], "f"), "rate_month": selection["rate_month"],
            "estimated": not selection["exact"], "inverted": bool(provenance.get("inverted")),
            "source": provenance.get("source", "Saved rate; source details not supplied"),
            "method": provenance.get("method", "inverse" if provenance.get("inverted") else "not supplied"),
        })
    return {"version": 1, "target": "USD", "rows": rows, "missing": missing,
            "definition": "Combined USD uses original native cost and native savings components. "
            "Each retained reporting-group amount is converted once with exact half-even 18-place arithmetic. "
            "Nearest saved months are estimated; list-price rates are not invoice FX. "
            "Credit-pool conversion uses the selected reporting month; Advisor uses its source update month."}


def update_report(
    current: Mapping[str, Any], incoming: Mapping[str, Any] | None = None,
    *, fx_rates: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if incoming is not None and any(
        current["meta"][name] != incoming["meta"][name] for name in ("basis", "currency")
    ):
        raise ValueError("Upload cost basis/reporting currency must match the current report; bases are never mixed")
    history = current.get("annual_history", current)
    if "annual_history" in history or history is not current and (
        "retention" in history or history.get("history_policy", {}).get("version") != 1
    ):
        raise ValueError("Annual history must be a standalone, untruncated cost snapshot")
    if any(history["meta"][name] != current["meta"][name] for name in ("basis", "currency")):
        raise ValueError("Annual history cost basis/reporting currency does not match the comparison report")
    existing_scopes = source_scopes(history)
    detail_scopes = source_scopes(current)
    if any(existing_scopes.get(identity) != value for identity, value in detail_scopes.items()):
        raise ValueError("Annual history and detailed comparison scopes disagree")
    new_scopes = source_scopes(incoming) if incoming else {}
    today = date.today().strftime("%Y-%m")
    if new_scopes and max(month for month, _account in new_scopes) > today:
        raise ValueError("Future billing months are refused before they can evict comparison data")
    overlap = set(existing_scopes) & set(new_scopes)
    for identity in overlap:
        if existing_scopes[identity][0] != new_scopes[identity][0]:
            raise ValueError("Replacement enrollment names must match the existing account identity")
    months = sorted({month for month, _account in existing_scopes.keys() | new_scopes.keys()})[-MONTH_LIMIT:]
    if not months:
        raise ValueError("The base report must contain cost data before uploading Advisor files")
    def sources(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        return snapshot["meta"].get("sources", [snapshot["meta"]])

    candidates = [(current, index) for index in range(len(sources(current)))]
    if incoming:
        known_hashes = {source["source_sha256"] for value in (current, history) for source in sources(value)}
        for source in sources(incoming):
            source_scopes_incoming = {
                (scope["month"], account)
                for scope in source.get("available_scopes", [])
                for account in source["billing_accounts"][scope["enrollment"]]
            }
            if source["source_sha256"] in known_hashes and not source_scopes_incoming - set(existing_scopes):
                raise ValueError("This exact file is already loaded; duplicate uploads are not added twice")
        candidates.extend((incoming, index) for index in range(len(sources(incoming))))
    cost_fragments = []
    for value in (history, incoming):
        if value is None:
            continue
        for index in range(len(sources(value))):
            keep = lambda month, account, original=value is history: not original or (month, account) not in new_scopes
            fragment = _fragment(value, index, keep, False)
            if fragment:
                cost_fragments.append(fragment)
    annual = mbr.combine_snapshots(cost_fragments)
    annual["history_policy"] = {
        "version": 1, "kind": "grouped-annual-history",
        "years": sorted({month[:4] for month in annual["dimensions"]["month"]}),
        "definition": "Grouped source costs and calculation inputs for annual/YTD reporting only; "
        "detailed comparisons retain two months. Calendar years remain separate and missing months are unavailable.",
    }
    dated_by_hash = {}
    for position, (snapshot, index) in enumerate(candidates):
        if any(item["source"] == index and item["rows"] for item in snapshot.get("advisor_snapshots", [])):
            source = sources(snapshot)[index]
            dated_by_hash[source["source_sha256"]] = (source.get("advisor_date_end") or "", position)
    dated = sorted(dated_by_hash.values(), reverse=True)
    recommendations = {}
    for _day, position in dated[:2]:
        value, index = candidates[position]
        fragment = _fragment(value, index, lambda _month, _account: False, True)
        if fragment:
            recommendations[fragment["meta"]["source_sha256"]] = fragment
    fragments = []
    for index in range(len(sources(annual))):
        fragment = _fragment(annual, index, lambda month, _account: month in months, False)
        if not fragment:
            continue
        recommendation = recommendations.pop(fragment["meta"]["source_sha256"], None)
        if recommendation:
            fragment["advisor"] = recommendation["advisor"]
            fragment["advisor_snapshots"][0]["rows"] = recommendation["advisor"]
            for meta in (fragment["meta"], fragment["meta"]["sources"][0]):
                for field in ("advisor_date_start", "advisor_date_end"):
                    meta[field] = recommendation["meta"][field]
        fragments.append(fragment)
    fragments.extend(recommendations.values())
    source_order = {
        source["source_sha256"]: index for index, source in enumerate(sources(current))
    }
    fragments.sort(key=lambda fragment: source_order.get(
        fragment["meta"]["source_sha256"], len(source_order),
    ))
    result = mbr.combine_snapshots(fragments)
    result["annual_history"] = annual
    previous_expired = current.get("retention", {}).get("expired_months_count", 0)
    dropped_months = sorted({month for month, _account in detail_scopes} - set(months))
    result["retention"] = {
        "kind": "newest-available-billing-months", "limit": MONTH_LIMIT, "months": months,
        "comparison_months": months, "advisor_snapshot_limit": 2,
        "expired_months_count": previous_expired + len(dropped_months),
        "updated_utc": datetime.now(UTC).isoformat(),
    }
    result["audit"]["warnings"].append({
        "code": "retention", "title": "Two-month comparison report",
        "detail": "Detailed comparisons use only the two newest supplied billing months. Older grouped "
        "data is kept separately for annual/YTD totals, credit/savings matrices and projections; it is "
        "not added to detailed comparisons. Advisor snapshots remain separate and capped at two. "
        "Source files and the main database are unchanged.",
    })
    changes = []
    for identity, (name, cost, rows) in sorted(new_scopes.items()):
        previous = existing_scopes.get(identity)
        changes.append({
            "month": identity[0], "account": identity[1], "enrollment": name,
            "previous_gbp": mbr.amount_string(previous[1]) if previous else None,
            "incoming_gbp": mbr.amount_string(cost), "previous_rows": previous[2] if previous else None,
            "incoming_rows": rows, "retained": identity[0] in months,
            "action": ("replace" if identity in overlap else "add")
            + (" comparison + annual" if identity[0] in months else " annual history only"),
        })
    result["report_fx_rates"] = deepcopy(fx_rates if fx_rates is not None else current.get("report_fx_rates", []))
    result["usd_fx"] = usd_fx_evidence(result, result["report_fx_rates"])
    return result, {
        "retained_months": months, "removed_months": dropped_months,
        "requires_replacement": bool(overlap), "changes": changes,
        "annual_months": annual["dimensions"]["month"], "annual_rows": annual["meta"]["source_rows"],
        "comparison_changed": bool(dropped_months or any(month in months for month, _account in new_scopes)),
        "retained_rows": result["meta"]["source_rows"],
        "advisor_snapshots": sum(bool(item["rows"]) for item in result["advisor_snapshots"]),
        "unpriced_rows": sum(row[3] for row in result["calculations"]["rows"]),
        "unknown_eligibility_rows": sum(row[11] for row in result["rows"] if result["dimensions"]["eligible"][row[8]] == mbr.MISSING),
    }


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()
