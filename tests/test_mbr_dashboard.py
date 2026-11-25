"""The standalone workbook adapter preserves signed money and names missing data."""
from __future__ import annotations

import importlib.util
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_mbr_dashboard", Path(__file__).resolve().parents[1] / "scripts" / "build_mbr_dashboard.py",
)
assert SPEC is not None and SPEC.loader is not None
mbr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mbr)


def profile(count=1):
    return {
        "source_name": "20260601_Test.xlsb", "source_sha256": "a" * 64,
        "extracted_utc": "2026-11-10T10:00:00+00:00", "data_rows": count,
        "sheets": {
            "Trend": {"start_row": 1, "start_column": 1, "values": [
                ["Based on Amortized Cost"], ["Currency: GBP (for ExampleUS exchange rate: 0.75)"],
            ]},
            "Credits": {"start_row": 3, "start_column": 2, "values": [
                [None, "Credits GBP"], ["ExampleUS", Decimal(100)], ["ExampleUK", Decimal(50)],
            ]},
            "First Page": {"start_row": 1, "start_column": 1, "values": [[-2146826281]]},
        },
    }


def cost(value="100", **overrides):
    row = {field: "" for field in mbr.SOURCE_FIELDS}
    row.update({
        "BillingPeriodStartDate": "46235.0", "BillingAccountId": "100001.0",
        "Enrolment": "ExampleUS", "MeterCategory": "Storage", "SubscriptionName": "sub-a",
        "IsAzureCreditEligible": "True", "PricingModel": "OnDemand", "ResourceLocation": "uksouth",
        "Sub Type": "Greenfield", "Utilization Type": "Used", "Usage Type": "Azure",
        "Cost": value, "Cost GBP": value, "Savings": "-999999999",
    })
    row.update(overrides)
    return row


def test_enrollment_labels_are_preserved_without_customer_specific_aliases():
    assert mbr.enrollment("  Arbitrary account  ") == "Arbitrary account"
    assert mbr.enrollment("Custom Name") == "Custom Name"
    assert mbr.enrollment(None) == mbr.MISSING


def advisor_sheet():
    headers = [
        "Recommendation", "Enrollment", "Sub", "Subscription Name", "Subscription ID",
        "Resource Name", "Business Impact", "Prod", "Service", "Savings GBP",
        "Potential Annual Cost Savings", "Potential Cost Savings Currency", "Updated Date",
    ]
    return {"start_row": 1, "start_column": 1, "values": [
        headers,
        ["Review", "ExampleUK", "sub-a", "sub-a (id)", "id", "resource", "High", "yes",
         "Storage", Decimal("12.34"), "12.34", "GBP", Decimal("46220.5")],
        ["Unmapped", None, None, "sub-b (id2)", "id2", "resource2", "High", None,
         None, None, "1,234.50", "USD", None],
    ]}


def test_signed_exact_totals_and_duplicates_are_preserved():
    rows = [cost("100.01"), cost("-20.005"), cost("0.005"), cost("100.01")]
    data = mbr.build_snapshot(profile(4), rows)
    assert len(data["rows"]) == 1
    assert data["rows"][0][-2:] == [str(180020000000000000000), 4]
    assert data["audit"]["reconciliations"][0]["source_gbp"] == "180.020"
    assert data["dimensions"]["month"] == ["2026-08"]
    assert data["meta"]["billing_accounts"] == {"ExampleUS": ["100001"]}
    assert "-999999999" not in json.dumps(data)


def test_template_formula_zeroes_are_not_example_uk_spend():
    blank = {field: "" for field in mbr.SOURCE_FIELDS}
    blank.update({"Enrolment": "ExampleUK", "Cost GBP": "0", "Sub Type": "Brownfield",
                  "Utilization Type": "Used", "Usage Type": "Azure", "Savings": "0"})
    blank.update({field: "" for _, field in mbr.DIMENSIONS if field not in blank})
    data = mbr.build_snapshot(profile(2), [cost(), blank])
    assert data["meta"]["source_rows"] == 1
    assert data["meta"]["template_rows"] == 1
    assert data["dimensions"]["enrollment"] == ["ExampleUS"]
    assert data["meta"]["missing_spend_enrollments"] == ["ExampleUK"]
    assert {w["code"] for w in data["audit"]["warnings"]} >= {
        "missing-enrollment", "template", "filename-period", "history", "savings", "excel-errors",
    }


def test_native_savings_are_calculated_from_prices_not_reverse_fx():
    declaration = profile(3)
    declaration["conversion_rates"] = {"ExampleUS": "0.75"}
    data = mbr.build_snapshot(declaration, [
        cost("10", **{"Cost GBP": "7.5", "BillingCurrency": "USD",
                     "PricingModel": "Reservation", "Unit": "3", "Quantity": "5"}),
        cost("2", **{"Cost GBP": "1.5", "BillingCurrency": "USD",
                    "PricingModel": "Reservation", "Utilization Type": "Unused"}),
        cost("4", **{"Cost GBP": "3", "BillingCurrency": "USD",
                    "PricingModel": "SavingsPlan"}),
    ])
    native = data["native_calculations"]["rows"]
    assert sum(int(row[0]) for row in native) == mbr.units("5", "expected")
    assert sum(row[2] for row in native) == 2
    assert sum(row[3] for row in native) == 1
    assert sum(int(row[0]) for row in data["calculations"]["rows"]) == mbr.units("3.75", "expected")
    assert data["row_native_currencies"] == ["USD"] * 3


def test_unknown_native_currency_does_not_create_valued_native_estimates():
    data = mbr.build_snapshot(profile(), [
        cost("10", PricingModel="Reservation", Unit="3", Quantity="5"),
    ])
    assert data["native_calculations"]["rows"] == [["0", "0", 0, 1]]


@pytest.mark.parametrize("field", ["BillingPeriodStartDate", "BillingAccountId", "Cost", "Cost GBP"])
def test_partial_rows_fail_instead_of_becoming_zero(field):
    with pytest.raises(ValueError, match=f"missing {field}"):
        mbr.build_snapshot(profile(), [cost(**{field: ""})])


@pytest.mark.parametrize("value", ["NaN", "Infinity", "#DIV/0!", "n/a", "100000000000000000000"])
def test_invalid_money_is_rejected(value):
    with pytest.raises(ValueError):
        mbr.build_snapshot(profile(), [cost(value)])


def test_precision_is_half_even_and_totals_reconcile_to_pennies():
    assert mbr.units("0.0000000000000000005", "test") == 0
    assert mbr.units("0.0000000000000000015", "test") == 2
    assert mbr.units("-0.0000000000000000015", "test") == -2
    assert mbr.units("99999999999999999999.999999999999999999", "test") == 10**38 - 1
    data = mbr.build_snapshot(profile(2), [
        cost("0.0100000000000000004"), cost("0.0000000000000000004"),
    ])
    r = data["audit"]["reconciliations"][0]
    assert r["dashboard_gbp"] == "0.01"
    assert Decimal(r["normalization_delta_gbp"]) == Decimal("-0.0000000000000000008")


def test_zero_is_valid_but_missing_data_is_not_zero():
    data = mbr.build_snapshot(profile(), [cost("0")])
    assert data["rows"][0][-2:] == ["0", 1]
    with pytest.raises(ValueError, match="No populated"):
        mbr.build_snapshot(profile(0), [])


def test_cost_basis_and_reporting_currency_are_required():
    p = profile()
    p["sheets"]["Trend"]["values"][0][0] = "No basis supplied"
    with pytest.raises(ValueError, match="cost basis"):
        mbr.build_snapshot(p, [cost()])
    p = profile()
    p["sheets"]["Trend"]["values"][1][0] = "Currency: USD"
    with pytest.raises(ValueError, match="GBP"):
        mbr.build_snapshot(p, [cost()])


def test_advisor_gaps_remain_visible_not_valued_at_zero_or_inferred():
    p = profile()
    p["sheets"]["Advisor Data Dump"] = advisor_sheet()
    data = mbr.build_snapshot(p, [cost()])
    first, second = data["advisor"]
    assert first["enrollment"] == "ExampleUK" and first["annual_gbp"] == "12340000000000000000"
    assert second["enrollment"] == mbr.MISSING and second["annual_gbp"] is None
    assert second["native_annual"] == "1234.50" and second["native_currency"] == "USD"
    assert second["subscription"] == "sub-b"
    assert data["meta"]["advisor_date_start"] == "2026-07-17"
    assert any(w["code"] == "advisor-gaps" for w in data["audit"]["warnings"])


def test_bad_row_counts_and_non_month_start_dates_fail():
    with pytest.raises(ValueError, match="row count"):
        mbr.build_snapshot(profile(2), [cost()])
    with pytest.raises(ValueError, match="month start"):
        mbr.build_snapshot(profile(), [cost(BillingPeriodStartDate="2026-08-02")])


def test_summary_errors_are_counted_not_included_in_cost():
    data = mbr.build_snapshot(profile(), [cost("25")])
    assert data["audit"]["excel_errors"] == [
        {"sheet": "First Page", "error": "#DIV/0!", "count": 1},
    ]
    assert data["rows"][0][-2] == str(25 * mbr.FACTOR)


def test_generated_html_is_offline_and_embedded_data_cannot_escape_its_script():
    data = mbr.build_snapshot(profile(), [cost(SubscriptionName="</script><img src=x onerror=alert(1)>")])
    html = mbr.render_html(data)
    assert "__MBR_DATA__" not in html and "__MBR_SCRIPT__" not in html
    assert "\\u003c/script\\u003e" in html
    assert "<img src=x" not in html
    assert "connect-src 'none'" in html
    assert 'src="http' not in html
    assert re.findall(r'href="(http[^"]+)"', html) == ["http://127.0.0.1:8766/#uploads"]
    assert "--cp-accent: #b11f4b;" in html
    assert 'id="mbr-data"' in html


def priced_profile(count=1):
    p = profile(count)
    p["source_sha256"] = "priced-source"
    p["sheets"]["Trend"]["values"][1][0] = "Currency: GBP (for ExampleUS exchange rate: 0.5)"
    p["data_headers"] = ["Cost GBP"]
    p["data_formulas"] = [["Cost GBP"], ['=IF(Q2="ExampleUS",O2*0.5,O2)']]
    return p


def test_corrected_estimate_uses_observed_cost_and_subtracts_unused_in_gbp():
    p = priced_profile(2)
    data = mbr.build_snapshot(p, [
        cost("80", **{"Cost GBP": "40", "PricingModel": "Reservation", "Unit": "12",
                     "Quantity": "10", "Effective": "999"}),
        cost("10", **{"Cost GBP": "5", "PricingModel": "Reservation", "Unit": "",
                     "Quantity": "", "Utilization Type": "Unused"}),
    ])
    gross = sum(int(row[0]) for row in data["calculations"]["rows"])
    assert gross == 20 * mbr.FACTOR
    assert sum(row[3] for row in data["calculations"]["rows"]) == 0
    assert data["meta"]["source_rows"] == 2
    assert gross - 5 * mbr.FACTOR == 15 * mbr.FACTOR


@pytest.mark.parametrize("model", ["OnDemand", "On Demand", "Spot"])
def test_noncovered_models_do_not_generate_spurious_savings(model):
    data = mbr.build_snapshot(priced_profile(), [
        cost("80", **{"Cost GBP": "40", "PricingModel": model, "Unit": "999999",
                     "Quantity": "1000"}),
    ])
    assert data["calculations"]["rows"] == [["0", "0", 0, 0]]


def test_missing_price_or_conversion_is_explicit_not_a_zero_estimate():
    data = mbr.build_snapshot(priced_profile(), [
        cost("80", **{"Cost GBP": "40", "PricingModel": "SavingsPlan", "Unit": "", "Quantity": "10"}),
    ])
    assert data["calculations"]["rows"][0][2:] == [0, 1]
    assert data["meta"]["calculation_issues"]
    p = profile()
    data = mbr.build_snapshot(p, [
        cost("80", PricingModel="Reservation", Unit="12", Quantity="10"),
    ])
    assert "conversion" in data["meta"]["calculation_issues"][0]["reason"]


def test_explicit_tier_discount_is_used_and_missing_discount_is_counted():
    p = priced_profile()
    p["sheets"]["Command Panel"] = {
        "start_row": 1, "start_column": 1,
        "values": [[None, None, None, None, "Product", "Discount"],
                   [None, None, None, None, "Commitment Tier A", Decimal("0.25")]],
    }
    data = mbr.build_snapshot(p, [cost("75", Product="Commitment Tier A")])
    assert data["calculations"]["rows"] == [["0", str(25 * mbr.FACTOR), 1, 0]]
    data = mbr.build_snapshot(p, [cost("75", Product="Commitment Tier missing")])
    assert data["calculations"]["rows"][0][2:] == [0, 1]


def test_disagreeing_conversion_label_fails_instead_of_guessing():
    p = priced_profile()
    p["sheets"]["Trend"]["values"][1][0] = "Currency: GBP (for ExampleUS exchange rate: 0.75)"
    with pytest.raises(ValueError, match="disagrees"):
        mbr.build_snapshot(p, [cost()])


def test_combining_nonoverlapping_sources_preserves_lineage_and_separate_advisor_snapshots():
    first = mbr.build_snapshot(profile(), [cost()])
    p = profile()
    p["source_sha256"] = "second-source"
    second = mbr.build_snapshot(p, [cost(BillingPeriodStartDate="2026-05-01")])
    combined = mbr.combine_snapshots([first, second])
    assert combined["meta"]["source_rows"] == 2
    assert combined["row_sources"] == [0, 1]
    assert combined["dimensions"]["month"] == ["2026-05", "2026-08"]
    assert len(combined["meta"]["sources"]) == 2
    assert len(combined["advisor_snapshots"]) == 2
    assert len(combined["calculations"]["rows"]) == 2
    assert len(first["meta"]["sources"]) == 1


def test_duplicate_overlapping_or_mixed_basis_sources_are_refused():
    first = mbr.build_snapshot(profile(), [cost()])
    with pytest.raises(ValueError, match="Duplicate"):
        mbr.combine_snapshots([first, first])
    p = profile()
    p["source_sha256"] = "different-file-same-month"
    second = mbr.build_snapshot(p, [cost()])
    with pytest.raises(ValueError, match="Overlapping"):
        mbr.combine_snapshots([first, second])
    second["meta"]["basis"] = "actual"
    with pytest.raises(ValueError, match="same cost basis"):
        mbr.combine_snapshots([first, second])


def test_publisher_lineage_does_not_merge_marketplace_into_azure():
    data = mbr.build_snapshot(profile(2), [
        cost("100", PublisherType="Azure"),
        cost("20", PublisherType="Marketplace"),
    ])
    assert len(data["rows"]) == 2
    assert data["row_publishers"] == ["Azure", "Marketplace"]
    assert sum(int(row[10]) for row in data["rows"]) == 120 * mbr.FACTOR
    assert data["meta"]["publisher_mismatch_rows"] == 1
    assert any(row["code"] == "publisher-classification" for row in data["audit"]["warnings"])


def test_trusted_usage_group_counts_preserve_raw_record_lineage():
    p = profile()
    data = mbr.build_snapshot(p, [cost(SourceRowCount="25", BillingCurrency="USD", ChargeType="Usage")], source_row_counts=True)
    assert data["meta"]["source_rows"] == 25
    assert data["meta"]["input_group_rows"] == 1
    assert data["rows"][0][11] == 25
    assert data["row_native_currencies"] == ["USD"]
    assert data["row_charge_types"] == ["Usage"]
    assert data["row_native_costs"] == [str(100 * mbr.FACTOR)]
    with pytest.raises(ValueError, match="usage-row count"):
        mbr.build_snapshot(p, [cost(SourceRowCount="-1")], source_row_counts=True)
