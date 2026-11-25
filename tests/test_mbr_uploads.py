"""Report uploads validate before mutation and retain exactly the requested months."""
from __future__ import annotations

import csv
import hashlib
import http.client
import importlib.util
import json
import sys
import threading
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

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
    service = load("serve_mbr_dashboard")


def snapshot(months=("2026-04", "2026-05", "2026-08"), account="a", name="Account A", digest="original"):
    profile = {
        "source_name": digest + ".xlsb", "source_sha256": digest,
        "extracted_utc": "2026-11-11T00:00:00+00:00", "data_rows": len(months),
        "sheets": {"Trend": {
            "start_row": 1, "start_column": 1,
            "values": [["Based on Amortized Cost"], ["Currency: GBP"]],
        }},
    }
    rows = []
    for month in months:
        row = {field: "" for field in mbr.SOURCE_FIELDS}
        row.update({
            "BillingPeriodStartDate": month + "-01", "BillingAccountId": account, "Enrolment": name,
            "MeterCategory": "Storage-" + month, "SubscriptionName": "sub-" + month,
            "ResourceLocation": "uksouth", "PricingModel": "OnDemand", "PublisherType": "Azure",
            "Cost": "100.005", "Cost GBP": "100.005", "Quantity": "1",
            "IsAzureCreditEligible": "True", "Sub Type": "Greenfield", "Usage Type": "Azure",
            "Utilization Type": "Used",
        })
        rows.append(row)
    return mbr.build_snapshot(profile, rows)


def csv_file(tmp_path, rows=None, name="cost.csv"):
    rows = rows or [{
        "Date": "09/01/2026", "BillingAccountId": "a", "MeterCategory": "Storage",
        "SubscriptionName": "sub", "PricingModel": "OnDemand", "PublisherType": "Azure",
        "CostInBillingCurrency": "12.345", "BillingCurrency": "GBP",
    }]
    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_cost(path, current=None, **options):
    return uploads.cost_csv(
        path, path.name, uploads.file_digest(path), current or snapshot(),
        "amortized", **options,
    )


def test_retention_physically_removes_old_rows_dimensions_and_reconciliations():
    original = snapshot()
    before = deepcopy(original)
    retained, preview = uploads.update_report(original)
    assert retained["dimensions"]["month"] == ["2026-05", "2026-08"]
    assert retained["meta"]["source_rows"] == 2
    assert preview["removed_months"] == ["2026-04"]
    assert "sub-2026-04" not in retained["dimensions"]["subscription"]
    assert "Storage-2026-04" not in retained["dimensions"]["service"]
    assert retained["annual_history"]["dimensions"]["month"] == ["2026-04", "2026-05", "2026-08"]
    assert {row["month"] for row in retained["audit"]["reconciliations"]} == {"2026-05", "2026-08"}
    assert retained["meta"]["sources"][0]["available_scopes"] == [
        {"month": "2026-05", "enrollment": "Account A"},
        {"month": "2026-08", "enrollment": "Account A"},
    ]
    assert original == before


def test_native_calculation_rows_remain_aligned_after_merge_retention_and_reload(tmp_path):
    original = snapshot()
    original["native_calculations"]["rows"] = [
        [str(mbr.units(str(index), "fixture")), "0", 1, 0] for index in (1, 2, 3)
    ]
    incoming = snapshot(("2026-09",), digest="new")
    incoming["native_calculations"]["rows"] = [[str(mbr.units("4", "fixture")), "0", 1, 0]]
    retained, _preview = uploads.update_report(original, incoming)
    target = tmp_path / "report.html"
    mbr.write_atomic(target, mbr.render_html(retained))
    reloaded = mbr.read_dashboard(target)
    assert reloaded["dimensions"]["month"] == ["2026-08", "2026-09"]
    assert [row[0] for row in reloaded["native_calculations"]["rows"]] == [
        str(mbr.units("3", "expected")), str(mbr.units("4", "expected")),
    ]


def test_newest_two_months_are_shared_across_enrollments_not_per_account():
    current = mbr.combine_snapshots([
        snapshot(("2026-05", "2026-08")),
        snapshot(("2026-04", "2026-05"), "b", "Account B", "second"),
    ])
    retained, _preview = uploads.update_report(current)
    assert retained["dimensions"]["month"] == ["2026-05", "2026-08"]
    assert {key for key in uploads.source_scopes(retained)} == {
        ("2026-05", "a"), ("2026-05", "b"), ("2026-08", "a"),
    }
    assert ("2026-08", "b") not in uploads.source_scopes(retained)


def test_new_month_evicts_oldest_and_keeps_signed_money_and_precise_counts():
    current, _ = uploads.update_report(snapshot())
    incoming = snapshot(("2026-09",), digest="new")
    incoming["rows"][0][10] = str(mbr.units("-3.125", "test"))
    result, preview = uploads.update_report(current, incoming)
    assert result["dimensions"]["month"] == ["2026-08", "2026-09"]
    assert preview["removed_months"] == ["2026-05"]
    assert uploads.source_scopes(result)["2026-09", "a"][1] == mbr.units("-3.125", "test")
    assert result["meta"]["source_rows"] == 2
    assert len(result["calculations"]["rows"]) == len(result["rows"]) == len(result["row_publishers"])


def test_scope_replacement_does_not_add_old_and_new_versions():
    current, _ = uploads.update_report(snapshot())
    incoming = snapshot(("2026-08",), digest="replacement")
    incoming["rows"][0][10] = str(mbr.units("25", "test"))
    result, preview = uploads.update_report(current, incoming)
    assert preview["requires_replacement"]
    assert preview["changes"][0]["previous_rows"] == 1
    assert uploads.source_scopes(result)["2026-08", "a"][1] == mbr.units("25", "test")
    assert result["meta"]["source_rows"] == 2
    assert uploads.source_scopes(current)["2026-08", "a"][1] == mbr.units("100.005", "test")


@pytest.mark.parametrize("incoming,error", [
    (lambda: snapshot(("2099-01",), digest="future"), "Future"),
    (lambda: snapshot(("2026-08",)), "already loaded"),
])
def test_expired_future_and_duplicate_uploads_fail_without_mutation(incoming, error):
    current, _ = uploads.update_report(snapshot())
    before = deepcopy(current)
    with pytest.raises(ValueError, match=error):
        uploads.update_report(current, incoming())
    assert current == before


def test_mixed_basis_and_conflicting_replacement_identities_are_refused():
    current, _ = uploads.update_report(snapshot())
    incoming = snapshot(("2026-09",), digest="actual")
    incoming["meta"]["basis"] = "actual"
    with pytest.raises(ValueError, match="basis"):
        uploads.update_report(current, incoming)
    incoming = snapshot(("2026-08",), name="Different", digest="rename")
    with pytest.raises(ValueError, match="names must match"):
        uploads.update_report(current, incoming)


def test_annual_backfill_updates_only_history_and_preserves_every_detail_row():
    current, _ = uploads.update_report(snapshot())
    before = deepcopy(current)
    result, preview = uploads.update_report(current, snapshot(("2026-01", "2026-02"), digest="backfill"))
    assert result["dimensions"] == before["dimensions"]
    for key in ("rows", "row_sources", "row_publishers", "row_native_costs", "calculations", "native_calculations"):
        assert result[key] == before[key]
    assert result["annual_history"]["dimensions"]["month"] == [
        "2026-01", "2026-02", "2026-04", "2026-05", "2026-08",
    ]
    assert not preview["comparison_changed"]
    assert not preview["requires_replacement"]
    assert all(change["action"] == "add annual history only" for change in preview["changes"])
    assert preview["annual_rows"] == 5
    assert preview["retained_rows"] == 2
    assert current == before


def test_annual_replacement_requires_confirmation_and_never_duplicates_scope_totals(store, tmp_path):
    row = {
        "Date": "2026-04-01", "BillingAccountId": "a", "MeterCategory": "Storage",
        "SubscriptionName": "sub", "PricingModel": "OnDemand", "PublisherType": "Azure",
        "Cost": "-5.25", "BillingCurrency": "GBP",
    }
    path = csv_file(tmp_path, [row])
    before = store.path.read_bytes()
    preview = store.validate(path, path.name, "cost", "amortized")
    assert preview["requires_replacement"]
    assert not preview["comparison_changed"]
    with pytest.raises(service.ReportConflict, match="Confirm"):
        store.commit(preview["id"], False)
    assert store.path.read_bytes() == before
    store.commit(preview["id"], True)
    saved = mbr.read_dashboard(store.path)
    assert saved["dimensions"]["month"] == ["2026-05", "2026-08"]
    assert uploads.source_scopes(saved["annual_history"])["2026-04", "a"][1] == mbr.units("-5.25", "expected")
    assert saved["annual_history"]["meta"]["source_rows"] == 3
    assert service.ReportStore(store.path).load()[0]["annual_history"]["rows"] == saved["annual_history"]["rows"]


def test_month_rollover_archives_the_outgoing_month_and_retains_previous_calendar_year():
    current, _ = uploads.update_report(snapshot(("2025-11", "2025-12")))
    result, _ = uploads.update_report(current, snapshot(("2026-01",), digest="january"))
    assert result["dimensions"]["month"] == ["2025-12", "2026-01"]
    assert result["annual_history"]["dimensions"]["month"] == ["2025-11", "2025-12", "2026-01"]
    assert result["annual_history"]["history_policy"]["years"] == ["2025", "2026"]


def test_corrupt_or_duplicate_annual_history_fails_without_mutating_current():
    current, _ = uploads.update_report(snapshot())
    duplicate = snapshot(("2026-04",))
    with pytest.raises(ValueError, match="already loaded"):
        uploads.update_report(current, duplicate)
    broken = deepcopy(current)
    broken["annual_history"]["rows"][-1][10] = "0"
    with pytest.raises(ValueError, match="scopes disagree"):
        uploads.update_report(broken)
    broken = deepcopy(current)
    broken["annual_history"]["history_policy"]["version"] = 2
    with pytest.raises(ValueError, match="Annual history"):
        uploads.update_report(broken)
    assert current["rows"][-1][10] == str(mbr.units("100.005", "expected"))


def test_original_workbook_can_restore_previously_expired_scopes_without_duplicating_advisor():
    original = snapshot()
    recommendations = [{"row": 2, "enrollment": "Account A", "updated": "2026-07-01"}]
    original["advisor"] = recommendations
    original["advisor_snapshots"][0]["rows"] = recommendations
    for metadata in (original["meta"], original["meta"]["sources"][0]):
        metadata["advisor_date_start"] = metadata["advisor_date_end"] = "2026-07-01"
    legacy, _ = uploads.update_report(original)
    legacy.pop("annual_history")
    restored, preview = uploads.update_report(legacy, original)
    assert restored["dimensions"]["month"] == ["2026-05", "2026-08"]
    assert restored["annual_history"]["dimensions"]["month"] == ["2026-04", "2026-05", "2026-08"]
    assert preview["requires_replacement"]
    assert sum(bool(item["rows"]) for item in restored["advisor_snapshots"]) == 1
    assert restored["advisor"] == recommendations


def test_usd_fx_evidence_persists_without_altering_native_or_stored_gbp_data(tmp_path):
    original = read_cost(csv_file(tmp_path))
    rates = [{"month": "2026-11", "from": "GBP", "to": "USD", "rate": "1.5", "source": "fixture", "method": "manual"}]
    before = deepcopy(original)
    result, _ = uploads.update_report(original, fx_rates=rates)
    for field in ("rows", "row_native_costs", "row_native_currencies", "calculations", "native_calculations"):
        assert result[field] == before[field]
    evidence = result["usd_fx"]["rows"][0]
    assert evidence == {
        "from": "GBP", "to": "USD", "month": "2026-09", "rate": "1.5",
        "rate_month": "2026-11", "estimated": True, "inverted": False,
        "source": "fixture", "method": "manual",
    }
    path = tmp_path / "report.html"
    mbr.write_atomic(path, mbr.render_html(result))
    reopened = service.ReportStore(path).load()[0]
    assert reopened["report_fx_rates"] == rates
    assert reopened["usd_fx"] == result["usd_fx"]
    assert original == before


def test_usd_fx_evidence_names_missing_currencies_and_keeps_inverse_provenance(tmp_path):
    original = read_cost(csv_file(tmp_path))
    missing = uploads.usd_fx_evidence(original, [])
    assert missing["rows"] == []
    assert missing["missing"] == [{"from": "GBP", "to": "USD", "month": "2026-09"}]
    inverse = uploads.usd_fx_evidence(original, [{
        "month": "2026-09", "from": "USD", "to": "GBP", "rate": "0.75",
        "source": "Saved manual fixture", "method": "manual",
    }])["rows"][0]
    assert inverse["rate"] == "1.333333333333333333"
    assert inverse["inverted"] and not inverse["estimated"]
    assert inverse["source"] == "Inverse of Saved manual fixture"
    assert inverse["method"] == "manual"


@pytest.mark.parametrize("rate", ["0", "NaN", "1.0000000000000000001", "1000000"])
def test_invalid_saved_usd_rates_refuse_report_updates(tmp_path, rate):
    original = read_cost(csv_file(tmp_path))
    before = deepcopy(original)
    with pytest.raises(ValueError):
        uploads.update_report(original, fx_rates=[{"month": "2026-09", "from": "GBP", "to": "USD", "rate": rate}])
    assert original == before


def test_local_service_loads_explicit_fx_file_and_rejects_malformed_rate_file(tmp_path):
    original = read_cost(csv_file(tmp_path))
    report = tmp_path / "report.html"
    mbr.write_atomic(report, mbr.render_html(original))
    rate_file = tmp_path / "fx.json"
    rate_file.write_text(json.dumps([{"month": "2026-09", "from": "GBP", "to": "USD", "rate": "1.25"}]), encoding="utf-8")
    store = service.ReportStore(report, rate_file)
    assert store.load()[0]["usd_fx"]["rows"][0]["rate"] == "1.25"
    before = report.read_bytes()
    rate_file.write_text("{}", encoding="utf-8")
    with pytest.raises(TypeError, match="list"):
        service.ReportStore(report, rate_file)
    assert report.read_bytes() == before


def test_azure_export_aliases_and_dates_are_supported_without_float_money(tmp_path):
    data = read_cost(csv_file(tmp_path))
    assert data["dimensions"]["month"] == ["2026-09"]
    assert data["dimensions"]["enrollment"] == ["Account A"]
    assert data["rows"][0][10] == str(mbr.units("12.345", "test"))
    assert data["dimensions"]["eligible"] == [mbr.MISSING]
    assert data["dimensions"]["portfolio"] == [mbr.MISSING]


def test_native_export_account_display_name_resolves_to_existing_id_without_renaming_history(tmp_path):
    row = {
        "Date": "2026-09-01", "BillingAccountId": "a", "BillingAccountName": "Long source-system name",
        "MeterCategory": "Storage", "SubscriptionName": "sub", "PricingModel": "OnDemand",
        "PublisherType": "Azure", "Cost": "10", "BillingCurrency": "GBP",
    }
    data = read_cost(csv_file(tmp_path, [row]))
    assert data["dimensions"]["enrollment"] == ["Account A"]


@pytest.mark.parametrize("column", uploads.REQUIRED_COST)
def test_each_missing_core_column_is_named_before_any_report_change(tmp_path, column):
    row = {
        "BillingPeriodStartDate": "2026-09", "BillingAccountId": "a", "MeterCategory": "Storage",
        "SubscriptionName": "sub", "PricingModel": "OnDemand", "PublisherType": "Azure", "Cost": "1",
        "BillingCurrency": "GBP",
    }
    del row[column]
    with pytest.raises(ValueError, match=column):
        read_cost(csv_file(tmp_path, [row]))


@pytest.mark.parametrize("headers,error", [
    ("Date,date,BillingAccountId\n1,1,a\n", "unique"),
    ("BilledCost,ChargeCategory\n1,Usage\n", "FOCUS"),
    ("Date,Cost,CostInBillingCurrency\n2026-09-01,1,1\n", "Ambiguous Cost"),
    ("Date,Cost\n2026-09-01\n", "field count"),
    ("Date,Cost\n", "no populated"),
])
def test_bad_csv_headers_and_record_shapes_fail_explicitly(tmp_path, headers, error):
    path = tmp_path / "bad.csv"
    path.write_text(headers, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        read_cost(path)


def test_foreign_currency_requires_saved_gbp_or_an_explicit_rate(tmp_path):
    path = csv_file(tmp_path)
    content = path.read_text().replace("GBP", "USD")
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="explicit"):
        read_cost(path)
    data = read_cost(path, rate_input="0.8")
    assert data["rows"][0][10] == str(mbr.units("9.876", "test"))
    assert "0.8" in data["meta"]["currency_label"]
    assert data["rows"][0][11] == 1


def test_saved_gbp_does_not_guess_a_foreign_baseline_conversion(tmp_path):
    row = {
        "Date": "2026-09-01", "BillingAccountId": "a", "MeterCategory": "Storage",
        "SubscriptionName": "sub", "PricingModel": "Reservation", "PublisherType": "Azure",
        "Cost": "10", "Cost GBP": "8", "BillingCurrency": "USD",
        "UnitPrice": "5", "Quantity": "5", "ChargeType": "Usage",
    }
    path = csv_file(tmp_path, [row])
    data = read_cost(path)
    assert data["calculations"]["rows"][0][3] == 1
    assert data["native_calculations"]["rows"][0] == [str(mbr.units("15", "test")), "0", 1, 0]
    data = read_cost(path, rate_input="0.8")
    assert data["calculations"]["rows"][0] == [str(mbr.units("12", "test")), "0", 1, 0]
    assert data["native_calculations"]["rows"][0] == [str(mbr.units("15", "test")), "0", 1, 0]
    with pytest.raises(ValueError, match="disagrees"):
        read_cost(path, rate_input="0.9")


def test_unknown_benefit_allocation_is_not_assumed_used(tmp_path):
    row = {
        "Date": "2026-09-01", "BillingAccountId": "a", "MeterCategory": "Storage",
        "SubscriptionName": "sub", "PricingModel": "Reservation", "PublisherType": "Azure",
        "Cost": "10", "BillingCurrency": "GBP", "UnitPrice": "5", "Quantity": "5",
    }
    data = read_cost(csv_file(tmp_path, [row]))
    assert data["calculations"]["rows"][0][2:] == [0, 1]


def advisor_file(tmp_path, day, filename="advisor.csv"):
    row = {name: "" for name in uploads.REQUIRED_ADVISOR}
    row.update({
        "Recommendation": "Review", "Subscription ID": "sub-id", "Subscription Name": "Sub",
        "Resource Name": "resource", "Business Impact": "High", "Updated Date": day,
        "Potential Annual Cost Savings": "1,200", "Potential Cost Savings Currency": "USD",
    })
    return csv_file(tmp_path, [row], filename)


@pytest.mark.parametrize("declaration", ["sep=,\r\n", '"sep=,"\r\n'])
def test_official_advisor_export_accepts_separator_refresh_date_and_unvalued_recommendations(tmp_path, declaration):
    rows = [{
        "Business Impact": "High", "Recommendation": "Review", "Subscription ID": "sub-id",
        "Subscription Name": "Sub (sub-id)", "Resource Name": "resource",
        "Last refreshed on": "2026-11-15T00:20:20.890127Z",
        "Potential Annual Cost Savings": "108", "Potential Cost Savings Currency": "USD",
    }, {
        "Business Impact": "Low", "Recommendation": "Review availability", "Subscription ID": "sub-id",
        "Subscription Name": "Sub (sub-id)", "Resource Name": "resource2",
        "Last refreshed on": "2026-11-16T10:57:23.0123323Z",
        "Potential Annual Cost Savings": "", "Potential Cost Savings Currency": "",
    }]
    path = csv_file(tmp_path, rows, "official-advisor.csv")
    path.write_text(declaration + path.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    original = path.read_bytes()
    current = snapshot()
    incoming = uploads.advisor_csv(path, path.name, uploads.file_digest(path), current, "amortized")
    assert len(incoming["advisor"]) == 2
    assert incoming["advisor"][0]["native_annual"] == "108"
    assert incoming["advisor"][0]["updated"] == "2026-11-15"
    assert incoming["advisor"][0]["subscription"] == "Sub"
    assert incoming["advisor"][1]["native_annual"] is None
    assert incoming["advisor"][1]["native_currency"] == mbr.MISSING
    assert incoming["advisor"][1]["updated"] == "2026-11-16"
    assert all(row["enrollment"] == mbr.MISSING for row in incoming["advisor"])
    assert "1 have no supplied annual monetary estimate" in incoming["audit"]["warnings"][0]["detail"]
    assert path.read_bytes() == original


def test_advisor_estimate_without_currency_and_ambiguous_refresh_dates_are_refused(tmp_path):
    rows = [{
        "Business Impact": "High", "Recommendation": "Review", "Subscription ID": "sub-id",
        "Subscription Name": "Sub", "Resource Name": "resource", "Updated Date": "2026-11-16",
        "Potential Annual Cost Savings": "0", "Potential Cost Savings Currency": "",
    }]
    path = csv_file(tmp_path, rows, "missing-currency.csv")
    with pytest.raises(ValueError, match="populated annual estimate requires"):
        uploads.advisor_csv(path, path.name, uploads.file_digest(path), snapshot(), "amortized")
    rows[0]["Potential Cost Savings Currency"] = "USD"
    rows[0]["Last refreshed on"] = "2026-11-15"
    path = csv_file(tmp_path, rows, "ambiguous-dates.csv")
    with pytest.raises(ValueError, match="Ambiguous Updated Date"):
        uploads.advisor_csv(path, path.name, uploads.file_digest(path), snapshot(), "amortized")


def test_advisor_upload_keeps_two_snapshots_separate_and_missing_mappings_explicit(tmp_path):
    current, _ = uploads.update_report(snapshot())
    for index, day in enumerate(("2026-08-01", "2026-09-01", "2026-10-01")):
        path = advisor_file(tmp_path, day, f"advisor-{index}.csv")
        incoming = uploads.advisor_csv(path, path.name, uploads.file_digest(path), current, "amortized")
        current, preview = uploads.update_report(current, incoming)
    snapshots = [item for item in current["advisor_snapshots"] if item["rows"]]
    assert len(snapshots) == preview["advisor_snapshots"] == 2
    assert {row["updated"] for item in snapshots for row in item["rows"]} == {"2026-09-01", "2026-10-01"}
    assert current["dimensions"]["month"] == ["2026-05", "2026-08"]
    assert all(row["annual_gbp"] is None and row["enrollment"] == mbr.MISSING for item in snapshots for row in item["rows"])
    assert "2026-08-01" not in json.dumps(snapshots)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "Dashboard.html"
    path.write_text(mbr.render_html(snapshot()), encoding="utf-8")
    return service.ReportStore(path)


def test_validation_is_read_only_until_apply_and_commit_persists_after_reload(store, tmp_path):
    original = store.path.read_bytes()
    path = csv_file(tmp_path)
    preview = store.validate(path, path.name, "cost", "amortized")
    assert store.path.read_bytes() == original
    assert preview["retained_months"] == ["2026-08", "2026-09"]
    assert store.commit(preview["id"], False)["applied"]
    persisted = mbr.read_dashboard(store.path)
    assert persisted["dimensions"]["month"] == ["2026-08", "2026-09"]
    reopened = service.ReportStore(store.path)
    assert reopened.load()[0]["dimensions"]["month"] == ["2026-08", "2026-09"]


def test_replacement_needs_confirmation_and_stale_previews_cannot_overwrite(store, tmp_path):
    path = csv_file(tmp_path)
    path.write_text(path.read_text().replace("09/01", "08/01"), encoding="utf-8")
    preview = store.validate(path, path.name, "cost", "amortized")
    assert preview["requires_replacement"]
    with pytest.raises(service.ReportConflict, match="Confirm"):
        store.commit(preview["id"], False)
    store.path.write_text(store.path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(service.ReportConflict, match="changed"):
        store.commit(preview["id"], True)


def test_failed_validation_and_failed_publication_preserve_the_existing_report(store, tmp_path, monkeypatch):
    before = store.path.read_bytes()
    bad = tmp_path / "bad.csv"
    bad.write_text("Wrong\nx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing"):
        store.validate(bad, bad.name, "cost", "amortized")
    assert store.path.read_bytes() == before
    path = csv_file(tmp_path)
    preview = store.validate(path, path.name, "cost", "amortized")
    def fail(_path, _content):
        raise OSError("test disk write failure")
    monkeypatch.setattr(mbr, "write_atomic", fail)
    with pytest.raises(OSError, match="disk write"):
        store.commit(preview["id"], False)
    assert store.path.read_bytes() == before


def test_hosted_html_allows_only_same_origin_requests_and_does_not_persist_the_token(store):
    snapshot_value = store.load()[0]
    hosted = mbr.render_html(snapshot_value, {"available": True, "token": "test-token", "origin": "http://127.0.0.1:8766"})
    assert "connect-src 'self'" in hosted
    assert "test-token" in hosted
    assert b"test-token" not in store.path.read_bytes()
    assert b"connect-src 'none'" in store.path.read_bytes()


def test_http_uploads_require_loopback_host_origin_token_and_explicit_preview_commit(store):
    server = service.ReportServer(("127.0.0.1", 0), store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(method, route, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            connection.request(method, route, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()
    try:
        status, body = request("GET", "/")
        assert status == 200 and b"test-token" not in body
        assert server.token.encode() in body
        assert request("GET", "/", headers={"Host": "evil.invalid"})[0] == 403
        assert request("POST", "/api/report/apply", b"{}")[0] == 403
        token = {"X-AzFinOps-Report": server.token}
        assert request("POST", "/api/report/apply", b"{}", {**token, "Origin": "https://evil.invalid"})[0] == 403
        csv_body = (
            b"Date,BillingAccountId,MeterCategory,SubscriptionName,PricingModel,PublisherType,Cost,BillingCurrency\r\n"
            b"2026-09-01,a,Storage,sub,OnDemand,Azure,10,GBP\r\n"
        )
        original = store.path.read_bytes()
        status, body = request("POST", "/api/report/validate?kind=cost&basis=amortized", csv_body, {
            **token, "X-Report-Filename": quote("upload.csv"), "Content-Type": "application/octet-stream",
        })
        assert status == 200, body
        preview = json.loads(body)
        assert store.path.read_bytes() == original
        status, body = request("POST", "/api/report/apply", json.dumps({"id": preview["id"], "replace": False}), {
            **token, "Content-Type": "application/json",
        })
        assert status == 200 and json.loads(body)["applied"]
        assert store.load()[0]["dimensions"]["month"] == ["2026-08", "2026-09"]
        assert request("GET", "/data/azfinops.duckdb")[0] == 404
        assert request("POST", "/api/report/validate", b"x", {
            **token, "X-Report-Filename": "../escape.csv", "Content-Type": "application/octet-stream",
        })[0] == 422
        assert request("POST", "/api/report/apply", b"{}", {**token, "Content-Type": "text/plain"})[0] == 422
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_persisted_revision_is_the_hash_of_the_same_bytes_used_for_the_snapshot(store):
    value, revision = store.load()
    assert revision == hashlib.sha256(store.path.read_bytes()).hexdigest()
    assert value["dimensions"]["month"] == ["2026-05", "2026-08"]
