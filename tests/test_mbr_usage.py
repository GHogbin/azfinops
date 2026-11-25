"""Power Query-compatible source grouping with exact, signed, traceable money."""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: module}):
        spec.loader.exec_module(module)
    return module


mbr = load("build_mbr_dashboard")
with patch.dict(sys.modules, {"build_mbr_dashboard": mbr}):
    uploads = load("mbr_uploads")
with patch.dict(sys.modules, {"build_mbr_dashboard": mbr, "mbr_uploads": uploads}):
    usage = load("ingest_mbr_usage")


@pytest.fixture
def con():
    connection = duckdb.connect()
    usage.install_decimal_normalizer(connection)
    yield connection
    connection.close()


def row(**overrides):
    value = {
        "BillingPeriodStartDate": "09/01/2026", "BillingAccountId": "a",
        "MeterCategory": "Storage", "Product": "Product A", "SubscriptionName": "a1a-production",
        "IsAzureCreditEligible": "True", "ProductOrderName": "", "PublisherType": "Azure",
        "PricingModel": "OnDemand", "ResourceLocation": "uksouth", "EffectivePrice": "1",
        "UnitPrice": "2", "BillingCurrency": "USD", "ChargeType": "Usage",
        "Date": "09/01/2026", "Quantity": "2", "Cost": "10",
    }
    return {**value, **overrides}


def csv_file(tmp_path, name="source.csv", rows=None):
    path = tmp_path / name
    values = rows or [row()]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, list(values[0]))
        writer.writeheader()
        writer.writerows(values)
    return path


def base_snapshot():
    profile = {
        "source_name": "base.xlsb", "source_sha256": "base",
        "extracted_utc": "2026-11-11T00:00:00+00:00", "data_rows": 2,
        "sheets": {"Trend": {
            "start_row": 1, "start_column": 1,
            "values": [["Based on Amortized Cost"], ["Currency: GBP"]],
        }},
    }
    rows = []
    for account, name in (("a", "Account A"), ("b", "Account B")):
        rows.append({
            **row(BillingPeriodStartDate="2026-08-01", BillingAccountId=account),
            "Enrolment": name, "Cost GBP": "10", "Effective": "1", "Unit": "2",
            "Usage Type": "Azure", "Sub Type": "Greenfield", "Utilization Type": "Used",
        })
    return mbr.build_snapshot(profile, rows)


@pytest.mark.parametrize("value", [
    "0.0000000000000000005", "0.0000000000000000015", "-0.0000000000000000005",
    "-0.0000000000000000015", "0.00000000000000000050000000000001",
    "0.00000000000000000149999999999999", "5e-19", "-5e-19", "1.5e-18",
    "99999999999999999999.999999999999999999", "123.4567890123456789012345",
])
def test_decimal_normalizer_matches_python_half_even_without_double_rounding(con, value):
    result = con.execute("SELECT mbr_decimal(?), typeof(mbr_decimal(?))", [value, value]).fetchone()
    assert result[0] == Decimal(mbr.amount_string(mbr.units(value, "fixture")))
    assert result[1] == "DECIMAL(38,18)"


def test_grouping_sums_quantities_and_signed_costs_without_deduplicating_legitimate_rows(con, tmp_path):
    path = csv_file(tmp_path, rows=[row(), row(), row(Cost="-2.5", Quantity="-1")])
    scopes = usage.group_file(con, path)
    assert scopes[0]["Cost"] == Decimal("17.5")
    assert scopes[0]["SourceRowCount"] == 3
    grouped = con.execute("SELECT Cost, Quantity, SourceRowCount FROM _mbr_groups WHERE _scope_total=0").fetchall()
    assert grouped == [(Decimal("17.5"), Decimal(3), 3)]


def test_prices_are_grouping_keys_and_currency_charge_type_are_preserved(con, tmp_path):
    path = csv_file(tmp_path, rows=[
        row(), row(UnitPrice="3"), row(ChargeType="Credit", Cost="-1"),
        row(BillingCurrency="GBP"),
    ])
    usage.group_file(con, path)
    groups = con.execute("SELECT UnitPrice, BillingCurrency, ChargeType FROM _mbr_groups WHERE _scope_total=0").fetchall()
    assert len(groups) == 4
    assert (Decimal(2), "USD", "Credit") in groups
    assert (Decimal(3), "USD", "Usage") in groups
    assert (Decimal(2), "GBP", "Usage") in groups


@pytest.mark.parametrize("overrides,error", [
    ({"Cost": "bad"}, "Invalid Cost"),
    ({"Quantity": "bad"}, "Invalid Quantity"),
    ({"BillingCurrency": "DOLLARS"}, "Invalid billing currency"),
    ({"BillingPeriodStartDate": "09/02/2026"}, "month start"),
    ({"Date": "10/01/2026"}, "outside billing month"),
    ({"IsAzureCreditEligible": "perhaps"}, "Invalid IsAzureCreditEligible"),
    ({"BillingAccountId": ""}, "Missing BillingAccountId"),
])
def test_invalid_source_rows_fail_before_publication(con, tmp_path, overrides, error):
    with pytest.raises(duckdb.Error, match=error):
        usage.group_file(con, csv_file(tmp_path, rows=[row(**overrides)]))


def test_missing_quantity_is_not_aggregated_as_complete_quantity(con, tmp_path):
    usage.group_file(con, csv_file(tmp_path, rows=[row(), row(Quantity="")]))
    quantity, missing = con.execute("SELECT Quantity,MissingQuantityRows FROM _mbr_groups WHERE _scope_total=0").fetchone()
    assert quantity is None and missing == 1


def test_grouped_snapshot_counts_original_records_and_keeps_native_charge_lineage(con, tmp_path):
    path = csv_file(tmp_path, rows=[row(Cost="10"), row(Cost="-2")])
    scopes = usage.group_file(con, path)
    current = base_snapshot()
    data = usage.build_file_snapshot(
        con, path, scopes, current,
        [{"from": "USD", "to": "GBP", "month": "2026-11", "rate": "0.75", "method": "manual", "source": "fixture"}],
        {"2026-09", "2026-10"},
    )
    assert data["meta"]["source_rows"] == 2
    assert data["meta"]["input_group_rows"] == 1
    assert data["rows"][0][10:] == [str(6 * mbr.FACTOR), 2]
    assert data["row_charge_types"] == ["Usage"]
    assert data["row_native_currencies"] == ["USD"]
    assert data["row_native_costs"] == [str(8 * mbr.FACTOR)]
    assert data["meta"]["fx_details"][0]["estimated"]
    assert data["meta"]["fx_details"][0]["rate_month"] == "2026-11"
    assert any(item["code"] == "estimated-fx" for item in data["audit"]["warnings"])
    assert current["meta"]["source_rows"] == 2


def test_missing_fx_is_not_zero_and_scope_mismatches_are_not_relabelled(con, tmp_path):
    path = csv_file(tmp_path)
    scopes = usage.group_file(con, path)
    with pytest.raises(ValueError, match="No saved"):
        usage.build_file_snapshot(con, path, scopes, base_snapshot(), [], {"2026-09", "2026-10"})
    with pytest.raises(ValueError, match="outside"):
        usage.build_file_snapshot(con, path, scopes, base_snapshot(), [], {"2025-09", "2025-10"})


def test_nonzero_purchase_is_preserved_and_flagged_for_a_declared_amortized_export(con, tmp_path):
    path = csv_file(tmp_path, rows=[row(BillingCurrency="GBP", ChargeType="Purchase", Cost="123.45")])
    scopes = usage.group_file(con, path)
    snapshot = usage.build_file_snapshot(con, path, scopes, base_snapshot(), [], {"2026-09", "2026-10"})
    assert snapshot["rows"][0][10] == str(mbr.units("123.45", "test"))
    assert snapshot["row_charge_types"] == ["Purchase"]
    assert any(item["code"] == "amortized-purchase" for item in snapshot["audit"]["warnings"])
    before = len(snapshot["audit"]["warnings"])
    usage.add_basis_warnings(snapshot)
    assert len(snapshot["audit"]["warnings"]) == before


def test_batch_publication_requires_coverage_or_an_explicit_missing_flag(tmp_path):
    current = base_snapshot()
    dashboard = tmp_path / "Dashboard.html"
    dashboard.write_text(mbr.render_html(current), encoding="utf-8")
    original = dashboard.read_bytes()
    before_current = deepcopy(current)
    files = [
        csv_file(tmp_path, "a-september.csv", [row()]),
        csv_file(tmp_path, "b-september.csv", [row(BillingAccountId="b", BillingCurrency="GBP")]),
        csv_file(tmp_path, "b-october.csv", [row(BillingAccountId="b", BillingCurrency="GBP", BillingPeriodStartDate="10/01/2026", Date="10/01/2026")]),
    ]
    fx_file = tmp_path / "fx.json"
    fx_file.write_text(json.dumps([{"from": "USD", "to": "GBP", "month": "2026-09", "rate": "0.75", "method": "manual", "source": "fixture"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="Missing enrollment"):
        usage.run(files, dashboard, fx_file, ["2026-09", "2026-10"], ["a", "b"], apply=True)
    assert dashboard.read_bytes() == original
    report = usage.run(files, dashboard, fx_file, ["2026-09", "2026-10"], ["a", "b"], apply=True, allow_missing=True)
    saved = mbr.read_dashboard(dashboard)
    assert saved["dimensions"]["month"] == ["2026-09", "2026-10"]
    assert report["missing_scopes"] == [{"month": "2026-10", "account": "a"}]
    assert saved["meta"]["source_rows"] == 3
    assert len(saved["audit"]["reconciliations"]) == 3
    assert any(item["code"] == "missing-usage-file" for item in saved["audit"]["warnings"])
    assert set(saved["row_native_currencies"]) == {"USD", "GBP"}
    assert current == before_current
    corrected = csv_file(tmp_path, "a-october.csv", [
        row(BillingPeriodStartDate="10/01/2026", Date="10/01/2026"),
    ])
    completed = usage.run(
        [corrected], dashboard, fx_file, ["2026-09", "2026-10"], ["a", "b"], apply=True,
    )
    final = mbr.read_dashboard(dashboard)
    assert completed["missing_scopes"] == []
    assert len(uploads.source_scopes(final)) == 4
    assert final["meta"]["source_rows"] == 4
    assert not any(item["code"] == "missing-usage-file" for item in final["audit"]["warnings"])


def test_annual_backfill_skips_unchanged_hashes_and_requires_confirmed_changed_replacements(tmp_path):
    dashboard = tmp_path / "Dashboard.html"
    current = base_snapshot()
    current["dimensions"]["month"] = ["2026-09", "2026-10"]
    for key in ("rows", "row_sources", "row_publishers", "row_native_costs", "row_native_currencies", "row_charge_types"):
        current[key] += deepcopy(current[key])
    for value in current["rows"][2:]:
        value[0] = 1
    for key in ("calculations", "native_calculations"):
        current[key]["rows"] += deepcopy(current[key]["rows"])
    current, _ = uploads.update_report(current)
    dashboard.write_text(mbr.render_html(current), encoding="utf-8")
    fx_file = tmp_path / "fx.json"
    fx_file.write_text(json.dumps([{"from": "USD", "to": "GBP", "month": "2026-09", "rate": "0.75", "method": "manual", "source": "fixture"}]), encoding="utf-8")
    path = csv_file(tmp_path, "january.csv", [
        row(BillingPeriodStartDate="01/01/2026", Date="01/01/2026"),
    ])
    original_detail = mbr.read_dashboard(dashboard)["rows"]
    report = usage.run([path], dashboard, fx_file, None, ["a", "b"], year=2026, apply=True)
    saved = mbr.read_dashboard(dashboard)
    assert saved["dimensions"]["month"] == ["2026-09", "2026-10"]
    assert original_detail == saved["rows"]
    assert report["applied"]
    before = dashboard.read_bytes()
    with patch.object(usage, "group_file", side_effect=AssertionError("Unchanged source must not be grouped")):
        repeated = usage.run([path], dashboard, fx_file, None, ["a", "b"], year=2026, apply=True)
    assert len(repeated["skipped"]) == 1
    assert not repeated["applied"]
    assert dashboard.read_bytes() == before
    update = uploads.update_report
    def corrupt_comparison(*args, **kwargs):
        result, preview = update(*args, **kwargs)
        result["rows"][0][10] = "0"
        return result, preview
    with (
        patch.object(uploads, "update_report", side_effect=corrupt_comparison),
        pytest.raises(ValueError, match="changed detailed rows"),
    ):
        usage.run([path], dashboard, fx_file, None, ["a", "b"], year=2026, apply=True)
    assert dashboard.read_bytes() == before
    path.write_text(path.read_text().replace(",10\n", ",11\n"), encoding="utf-8")
    with pytest.raises(ValueError, match="--replace"):
        usage.run([path], dashboard, fx_file, None, ["a", "b"], year=2026, apply=True)
    assert dashboard.read_bytes() == before
    replacement = usage.run([path], dashboard, fx_file, None, ["a", "b"], year=2026, apply=True, replace=True)
    assert replacement["applied"]
    assert len(uploads.source_scopes(mbr.read_dashboard(dashboard)["annual_history"])) == 5


def test_owned_spill_cleanup_retries_transient_windows_locks(monkeypatch, caplog):
    class Temporary:
        attempts = 0

        def cleanup(self):
            self.attempts += 1
            if self.attempts == 1:
                raise PermissionError("test lock")
    temporary = Temporary()
    waits = []
    monkeypatch.setattr(usage.time, "sleep", waits.append)
    usage.cleanup_spill(temporary)
    assert temporary.attempts == 2 and waits == [0.2]
    assert "retrying cleanup" in caplog.text


def test_owned_spill_cleanup_failure_is_never_silent(monkeypatch):
    class Temporary:
        attempts = 0

        def cleanup(self):
            self.attempts += 1
            raise PermissionError("persistent test lock")
    temporary = Temporary()
    monkeypatch.setattr(usage.time, "sleep", lambda _seconds: None)
    with pytest.raises(PermissionError, match="persistent"):
        usage.cleanup_spill(temporary)
    assert temporary.attempts == 5
