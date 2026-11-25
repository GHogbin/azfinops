import pytest

from azfinops.ingestion.mapping import MappingError, build_typed_sql, detect

EA_MIN = ["BillingAccountId", "Date", "Cost", "BillingCurrency", "ChargeType"]


def test_bom_and_case_insensitive_headers():
    cm = detect(["﻿billingaccountid", "DATE", "cost", "BILLINGCURRENCY", "chargetype", "Extra"])
    assert cm.columns["usage_date"] == "DATE" and cm.unmapped == ["Extra"]


def test_cost_aliases_cover_ea_and_mca_names():
    for name in ("Cost", "CostInBillingCurrency", "costInBillingCurrency"):
        h = ["BillingAccountId", "Date", name, "BillingCurrency", "ChargeType"]
        assert detect(h).columns["cost"] == name


def test_focus_is_rejected_with_actionable_message():
    with pytest.raises(MappingError, match="FOCUS"):
        detect(EA_MIN + ["BilledCost"])


def test_missing_required_columns_are_all_named():
    with pytest.raises(MappingError) as e:
        detect(["Date"])
    for c in ("cost", "billing_currency", "charge_type", "billing_account_id"):
        assert c in str(e.value)


def test_sql_builder_quotes_identifiers_and_tolerates_absent_columns():
    h = ["BillingAccountId", "Date", "Cost", "BillingCurrency", "ChargeType", 'We"ird']
    sql = build_typed_sql(detect(h), synthetic_default=False)
    assert '"Cost"' in sql and "We" not in sql  # unmapped columns never reach the SQL
    assert "CREATE OR REPLACE TEMP TABLE _typed" in sql


def test_generated_sql_never_references_raw_headers_after_the_first_stage():
    """Regression: DuckDB matches names case-insensitively, so "Quantity" silently bound to the
    typed `quantity` column and failed. Raw headers may only appear in the first CTE (s1)."""
    h = ["BillingAccountId", "Date", "Cost", "BillingCurrency", "ChargeType", "Quantity", "UnitPrice",
         "PayGPrice", "PricingModel", "Tags"]
    sql = build_typed_sql(detect(h), synthetic_default=False)
    later = sql.split(" s2 AS ")[1]
    assert not [n for n in h if f'"{n}"' in later]


def test_payg_price_can_be_left_out_so_the_contract_price_is_the_baseline():
    h = EA_MIN + ["UnitPrice", "PayGPrice", "BillingAccountName", "MeterRegion", "MeterSubCategory", "EffectivePrice",
                  "ResourceName"]
    on, off = detect(h), detect(h, use_payg_price=False)
    assert on.columns["payg_price"] == "PayGPrice" and not on.contract_baseline
    assert off.columns["payg_price"] is None and off.contract_baseline and "PayGPrice" in off.unmapped
    for c, name in (("billing_account_name", "BillingAccountName"), ("meter_region", "MeterRegion"),
                    ("meter_subcategory", "MeterSubCategory"), ("effective_price", "EffectivePrice"),
                    ("resource_name", "ResourceName")):
        assert off.columns[c] == name and name not in off.unmapped
    sql = build_typed_sql(off, synthetic_default=False)
    assert "'contract'" in sql and '"PayGPrice"' not in sql
