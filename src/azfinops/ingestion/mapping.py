"""Header mapping and SQL typing/normalisation for CSV imports.

Columns are matched by case-insensitive alias, not by a hard-coded schema version. Verified against
a real EA *amortized* export header (55 columns, MM/DD/YYYY dates, PricingModel 'OnDemand', Tags
without outer braces, PayGPrice 0 on reservation rows). MCA aliases come from Microsoft's published
column list and are UNTESTED against a real MCA file.
"""
from __future__ import annotations

from dataclasses import dataclass

# canonical name -> aliases (lower-case). The canonical name itself is always accepted too.
ALIASES: dict[str, tuple[str, ...]] = {
    "usage_date": ("date", "usagedate"),
    "billing_account_id": ("billingaccountid",),
    "billing_currency": ("billingcurrency", "billingcurrencycode"),
    "subscription_id": ("subscriptionid", "subscriptionguid"),
    "resource_group": ("resourcegroup", "resourcegroupname"),
    "resource_id": ("resourceid",),
    "region": ("resourcelocation", "location"),
    "service_family": ("servicefamily",),
    "meter_category": ("metercategory",),
    "meter_id": ("meterid",),
    "meter_name": ("metername",),
    "sku": ("sku",),
    "unit": ("unitofmeasure",),
    "quantity": ("quantity", "usagequantity"),
    "unit_price": ("unitprice",),
    "payg_price": ("paygprice", "marketprice"),
    "billing_account_name": ("billingaccountname",),
    "meter_region": ("meterregion",),
    "meter_subcategory": ("metersubcategory",),
    "effective_price": ("effectiveprice",),
    "resource_name": ("resourcename",),
    "cost": ("cost", "costinbillingcurrency", "pretaxcost"),
    "charge_type": ("chargetype",),
    "pricing_model": ("pricingmodel",),
    "publisher_type": ("publishertype",),
    "publisher_name": ("publishername",),
    "frequency": ("frequency",),
    "reservation_id": ("reservationid",),
    "reservation_name": ("reservationname",),
    "benefit_id": ("benefitid",),
    "tags": ("tags",),
    "consumed_service": ("consumedservice",),
    "offer_id": ("offerid",),
    "term": ("term",),
    "synthetic": ("x_synthetic",),
}
REQUIRED = ("usage_date", "cost", "billing_currency", "charge_type", "billing_account_id")
# Presence of any of these means a FOCUS export (not supported).
FOCUS_MARKERS = {"billedcost", "chargecategory", "effectivecost", "contractedcost", "chargeclass"}

FACT_COLS = [
    "usage_date", "billing_account_id", "billing_currency", "subscription_id", "resource_group",
    "resource_id", "region", "service_family", "meter_category", "meter_id", "meter_name", "sku",
    "unit", "quantity", "unit_price", "payg_price", "payg_basis", "cost", "charge_type",
    "pricing_model", "publisher_type", "publisher_name", "frequency", "reservation_id",
    "reservation_name", "benefit_id", "tags", "consumed_service", "offer_id", "term", "is_synthetic",
    "billing_account_name", "meter_region", "meter_subcategory", "effective_price", "resource_name",
]


class MappingError(ValueError):
    """The file's header cannot be imported; the message says exactly why."""


@dataclass(frozen=True)
class ColumnMap:
    columns: dict[str, str | None]   # canonical -> actual header name (None if absent)
    unmapped: list[str]              # header columns that are ignored
    contract_baseline: bool = False  # PayGPrice not imported: UnitPrice (contract) is the price baseline

    @property
    def n_mapped(self) -> int:
        return sum(v is not None for v in self.columns.values())


def clean(name: str) -> str:
    return name.lstrip("﻿").strip()


def detect(header: list[str], use_payg_price: bool = True) -> ColumnMap:
    lower = {clean(h).lower(): h for h in header}
    if FOCUS_MARKERS & lower.keys():
        raise MappingError("This looks like a FOCUS export (e.g. BilledCost/ChargeCategory). FOCUS is "
                           "not supported; export 'Cost and usage details (amortized)' or '(actual)'.")
    cols: dict[str, str | None] = {}
    used: set[str] = set()
    for canon, aliases in ALIASES.items():
        if canon == "payg_price" and not use_payg_price:
            cols[canon] = None                       # PayGPrice is deliberately not imported
            continue
        hit = next((lower[a] for a in (canon, *aliases) if a in lower), None)
        cols[canon] = hit
        if hit:
            used.add(hit)
    missing = [c for c in REQUIRED if cols[c] is None]
    if missing:
        raise MappingError("Missing required column(s): " + "; ".join(
            f"{c} (accepted headers: {', '.join((c, *ALIASES[c]))})" for c in missing))
    return ColumnMap(cols, [h for h in header if h not in used], contract_baseline=not use_payg_price)


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def build_typed_sql(cm: ColumnMap, synthetic_default: bool) -> str:
    """CREATE TEMP TABLE _typed from registered Arrow table _raw (all columns VARCHAR, plus _rn)."""
    def txt(c: str) -> str:
        col = cm.columns.get(c)
        return f"NULLIF(trim({q(col)}), '')" if col else "CAST(NULL AS VARCHAR)"

    def num(c: str) -> str:
        return f"TRY_CAST({txt(c)} AS DECIMAL(38,18))"

    raw_date = txt("usage_date")
    exprs = {
        "usage_date": ("CAST(COALESCE(try_strptime(" + raw_date + ", '%m/%d/%Y'), "
                       "try_strptime(" + raw_date + ", '%Y-%m-%d'), "
                       "try_strptime(" + raw_date + ", '%Y-%m-%dT%H:%M:%S')) AS DATE)"),
        "billing_account_id": f"COALESCE({txt('billing_account_id')}, '(unknown)')",
        "billing_currency": f"upper({txt('billing_currency')})",
        "subscription_id": f"lower({txt('subscription_id')})",
        "resource_group": txt("resource_group"),
        "resource_id": f"lower({txt('resource_id')})",
        "region": txt("region"), "service_family": txt("service_family"),
        "meter_category": txt("meter_category"), "meter_id": txt("meter_id"),
        "meter_name": txt("meter_name"), "sku": txt("sku"), "unit": txt("unit"),
        "quantity": num("quantity"), "unit_price": num("unit_price"), "_payg_raw": num("payg_price"),
        "cost": num("cost"), "charge_type": txt("charge_type"),
        "_pm_raw": txt("pricing_model"),
        "publisher_type": txt("publisher_type"), "publisher_name": txt("publisher_name"),
        "frequency": txt("frequency"), "reservation_id": txt("reservation_id"),
        "reservation_name": txt("reservation_name"), "benefit_id": txt("benefit_id"),
        "_tags_raw": txt("tags"), "consumed_service": txt("consumed_service"),
        "offer_id": txt("offer_id"), "term": txt("term"),
        "billing_account_name": txt("billing_account_name"), "meter_region": txt("meter_region"),
        "meter_subcategory": txt("meter_subcategory"), "effective_price": num("effective_price"),
        "resource_name": txt("resource_name"),
        "is_synthetic": (f"COALESCE(lower({txt('synthetic')}) = 'true', "
                         f"{'TRUE' if synthetic_default else 'FALSE'})"),
    }
    for c in ("usage_date", "cost", "billing_currency", "quantity", "unit_price", "payg_price"):
        exprs[f"_t_{c}"] = txt(c)   # raw text kept for validation messages
    step1 = ", ".join(f"{e} AS {n}" for n, e in exprs.items())
    pm = ("CASE lower(replace(_pm_raw, ' ', '')) WHEN 'ondemand' THEN 'On Demand' "
          "WHEN 'reservation' THEN 'Reservation' WHEN 'savingsplan' THEN 'SavingsPlan' "
          "WHEN 'spot' THEN 'Spot' ELSE _pm_raw END")
    # PayG rule: positive PayGPrice is used as is. On Reservation/SavingsPlan rows PayGPrice is 0 in the
    # real export, so a positive UnitPrice is used as a labelled 'contract' basis. Otherwise unknown.
    # A literal 0 PayGPrice is kept only for non-benefit rows (genuinely free meters).
    if cm.contract_baseline:
        # No PayGPrice: the baseline is UnitPrice, the contract price. A positive UnitPrice is used on every row; a
        # zero UnitPrice is kept only for non-benefit rows (genuinely free meters). Otherwise unknown.
        base = (
            "CASE WHEN unit_price > 0 THEN unit_price "
            "WHEN NOT pricing_model_b AND unit_price = 0 THEN CAST(0 AS DECIMAL(38,18)) END AS payg_price, "
            "CASE WHEN unit_price > 0 OR (NOT pricing_model_b AND unit_price = 0) THEN 'contract' END AS payg_basis, "
        )
    else:
        base = (
            "CASE WHEN _payg_raw > 0 THEN _payg_raw "
            "WHEN pricing_model_b AND unit_price > 0 THEN unit_price "
            "WHEN NOT pricing_model_b AND _payg_raw = 0 THEN CAST(0 AS DECIMAL(38,18)) END AS payg_price, "
            "CASE WHEN _payg_raw > 0 THEN 'payg' "
            "WHEN pricing_model_b AND unit_price > 0 THEN 'contract' "
            "WHEN NOT pricing_model_b AND _payg_raw = 0 THEN 'payg' END AS payg_basis, "
        )
    step2 = (
        "SELECT *, " + pm + " AS pricing_model, " + base +
        "CASE WHEN _tags_raw IS NULL THEN NULL "
        "WHEN json_valid(CASE WHEN left(_tags_raw,1) = '{' THEN _tags_raw ELSE '{' || _tags_raw || '}' END) "
        "THEN (CASE WHEN left(_tags_raw,1) = '{' THEN _tags_raw ELSE '{' || _tags_raw || '}' END) END AS tags, "
        "(_tags_raw IS NOT NULL AND NOT json_valid(CASE WHEN left(_tags_raw,1) = '{' THEN _tags_raw "
        "ELSE '{' || _tags_raw || '}' END)) AS bad_tags "
        "FROM (SELECT *, COALESCE(" + pm + " IN ('Reservation','SavingsPlan'), FALSE) AS pricing_model_b FROM s1)"
    )
    checks = [
        ("usage_date IS NULL", "invalid_date", "date", "_t_usage_date"),
        ("cost IS NULL", "invalid_cost", "cost", "_t_cost"),
        ("billing_currency IS NULL OR NOT regexp_full_match(billing_currency, '[A-Z]{3}')",
         "invalid_currency", "billing_currency", "_t_billing_currency"),
        ("charge_type IS NULL", "missing_charge_type", "charge_type", "CAST(NULL AS VARCHAR)"),
        ("_t_quantity IS NOT NULL AND quantity IS NULL", "invalid_quantity", "quantity", "_t_quantity"),
        ("_t_unit_price IS NOT NULL AND unit_price IS NULL", "invalid_unit_price", "unit_price",
         "_t_unit_price"),
        ("_t_payg_price IS NOT NULL AND _payg_raw IS NULL", "invalid_payg_price", "payg_price",
         "_t_payg_price"),
    ]
    code = "CASE " + " ".join(f"WHEN {c} THEN '{k}'" for c, k, _, _ in checks) + " END"
    col = "CASE " + " ".join(f"WHEN {c} THEN '{n}'" for c, _, n, _ in checks) + " END"
    val = "CASE " + " ".join(f"WHEN {c} THEN {v}" for c, _, _, v in checks) + " END"
    return (f"CREATE OR REPLACE TEMP TABLE _typed AS WITH s1 AS (SELECT _rn, {step1} FROM _raw), "
            f"s2 AS ({step2}) SELECT *, {code} AS reject_code, {col} AS reject_col, "
            f"CAST({val} AS VARCHAR) AS reject_val FROM s2")
