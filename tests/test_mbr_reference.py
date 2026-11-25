"""Second-source verification does not force agreement or invent missing evidence."""
from __future__ import annotations

import copy
import csv
import hashlib
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

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
    reference = load("verify_mbr_reference")


def scope():
    result = reference.SourceScope("2026-05", "account-a", "ExampleUS")
    result.native = Decimal(1000)
    result.gbp = Decimal(1000)
    result.rows = 2
    result.groups["Storage", "OnDemand"] = Decimal(1000)
    return result


def explorer(*, adjustment=False, total=None):
    rows = [{"keys": ["Storage", "On Demand"], "cost": "1000.0000", "rows": 2}]
    if adjustment:
        rows.append({"keys": ["RoundingAdjustment", "On Demand"], "cost": "-1.2500", "rows": 1})
    return {
        "group_by": ["service", "pricing_model"], "groups": len(rows),
        "page": 1, "pages": 1, "page_size": 200,
        "total": total or ("998.7500" if adjustment else "1000.0000"), "rows": rows,
    }


def adjustment_evidence():
    return {
        "group_by": ["charge_type", "publisher_type"], "groups": 1, "total": "-1.2500",
        "rows": [{"keys": ["RoundingAdjustment", "Azure"], "cost": "-1.2500", "rows": 1}],
    }


def snapshot(month="2026-08", digest="b" * 64):
    values = [
        month, "ExampleUS", "Storage", "sub-a", "uksouth", "OnDemand",
        "Greenfield", "Used", "Yes", "Azure",
    ]
    return {
        "version": 1, "scale": 18, "dimension_order": [name for name, _ in mbr.DIMENSIONS],
        "dimensions": {name: [values[index]] for index, (name, _) in enumerate(mbr.DIMENSIONS)},
        "rows": [[0] * 10 + [str(1000 * mbr.FACTOR), 2]],
        "meta": {
            "source_name": "Test_Source.xlsb", "source_sha256": digest, "basis": "amortized",
            "currency": "GBP", "currency_label": "Currency: GBP",
            "billing_accounts": {"ExampleUS": ["account-a"]},
            "advisor_date_start": "2026-06-08", "advisor_date_end": "2026-06-09",
        },
    }


def test_native_costs_and_explicit_pricing_aliases_match():
    result = reference.compare_scope(scope(), explorer(), "GBP")
    assert result["status"] == "matched"
    assert result["shared_groups"] == result["matched_groups"] == 1
    assert result["total"]["difference"] == 0


def test_signed_adjustment_is_explained_but_never_removed():
    api = explorer(adjustment=True)
    before = copy.deepcopy(api)
    result = reference.compare_scope(scope(), api, "GBP", adjustment_evidence())
    assert result["status"] == "explained_difference"
    assert result["total"]["dashboard"] == Decimal("998.75")
    assert result["total"]["difference"] == Decimal("1.25")
    assert result["extra_groups"][0]["reference"] is None
    assert result["extra_groups"][0]["dashboard"] == Decimal("-1.25")
    assert result["adjusted_comparison"]["matched"]
    assert api == before


def test_adjustment_label_without_charge_evidence_is_not_certification():
    assert reference.compare_scope(scope(), explorer(adjustment=True), "GBP")["status"] == "different"
    evidence = adjustment_evidence()
    evidence["rows"][0]["keys"][0] = "Usage"
    assert reference.compare_scope(scope(), explorer(adjustment=True), "GBP", evidence)["status"] == "different"


def test_offsetting_group_errors_do_not_pass_because_total_matches():
    data = scope()
    data.groups["Storage", "OnDemand"] = Decimal(900)
    data.groups["Virtual Machines", "Reservation"] = Decimal(100)
    api = explorer()
    api["rows"] = [
        {"keys": ["Storage", "OnDemand"], "cost": "800", "rows": 1},
        {"keys": ["Virtual Machines", "Reservation"], "cost": "200", "rows": 1},
    ]
    api["groups"] = 2
    result = reference.compare_scope(data, api, "GBP")
    assert result["total"]["matched"]
    assert result["status"] == "different" and result["matched_groups"] == 0


def test_absent_zero_group_is_structural_not_an_invented_api_zero():
    data = scope()
    data.groups["Unassigned", "OnDemand"] = Decimal(0)
    result = reference.compare_scope(data, explorer(), "GBP")
    assert result["status"] == "matched"
    assert result["extra_groups"] == [{
        "service": "Unassigned", "pricing_model": "OnDemand", "reference": Decimal(0),
        "dashboard": None, "status": "zero_only_group", "present_in": "workbook",
    }]


def test_api_precision_boundary_is_not_mislabeled_as_cost_error():
    result = reference.compare_amounts(Decimal("214.925013440841537"), Decimal("214.9250"))
    assert result["matched"]
    assert result["penny_rounding_boundary"]
    assert result["difference"] == Decimal("0.000013440841537")
    assert not reference.compare_amounts(Decimal("1.0002"), Decimal("1.0000"))["matched"]


def test_truncated_groups_and_alias_collisions_fail_explicitly():
    api = explorer()
    api["groups"] = 2
    with pytest.raises(ValueError, match="pagination"):
        reference.compare_scope(scope(), api, "GBP")
    api["rows"].append({"keys": ["Storage", "OnDemand"], "cost": "0", "rows": 1})
    with pytest.raises(ValueError, match="aliases"):
        reference.compare_scope(scope(), api, "GBP")


def test_nonoverlapping_snapshots_do_not_verify_each_other_or_merge():
    first, second = snapshot("2026-05", "a" * 64), snapshot()
    before = copy.deepcopy(second)
    result = reference.compare_snapshots(first, second)
    assert result["status"] == "not_comparable" and result["checks"] == []
    assert result["reference_months"] == ["2026-05"]
    assert result["dashboard_months"] == ["2026-08"]
    assert second == before


def test_same_source_and_different_fx_or_basis_do_not_pass_as_independent():
    first, second = snapshot(), snapshot()
    assert reference.compare_snapshots(first, second)["status"] == "same_source"
    second["meta"]["source_sha256"] = "c" * 64
    second["meta"]["currency_label"] = "Currency: GBP; other rate"
    assert reference.compare_snapshots(first, second)["status"] == "not_comparable"
    second["meta"]["currency_label"] = first["meta"]["currency_label"]
    second["meta"]["basis"] = "actual"
    assert reference.compare_snapshots(first, second)["status"] == "not_comparable"


def test_reference_already_in_a_bundle_is_not_called_independent():
    first, second = snapshot("2026-05", "a" * 64), snapshot("2026-05", "bundle")
    second["meta"]["sources"] = [{"source_sha256": first["meta"]["source_sha256"]}]
    assert reference.compare_snapshots(first, second)["status"] == "same_source"


def test_native_currency_never_defaults_or_converts():
    metadata = {
        "billing_account_currencies": {"account-a": ["USD"]},
        "scopes": [{"currency": "USD", "cost_kind": "amortized", "synthetic": False}],
    }
    assert reference.native_currency(metadata, "account-a", "amortized") == "USD"
    metadata["billing_account_currencies"]["account-a"].append("GBP")
    with pytest.raises(ValueError, match="ambiguous"):
        reference.native_currency(metadata, "account-a", "amortized")


@pytest.mark.parametrize("base", [
    "https://127.0.0.1:8765", "http://example.com", "http://127.0.0.1.evil",
    "http://user:pass@127.0.0.1:8765", "http://127.0.0.1:8765/path",
    "http://127.0.0.1:8765?url=remote",
])
def test_verification_cannot_contact_remote_services(base):
    with pytest.raises(ValueError, match="loopback"):
        reference.LocalApi(base)


def test_api_redirect_is_an_error_not_an_outbound_followup(monkeypatch):
    requests = []

    class Response:
        status = 302

        def read(self, limit):
            return b"redirect"

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, headers):
            requests.append((method, path))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(reference.http.client, "HTTPConnection", Connection)
    with pytest.raises(RuntimeError, match="HTTP 302"):
        reference.LocalApi("http://127.0.0.1:8765").get("/api/meta")
    assert requests == [("GET", "/api/meta")]


def test_pagination_requires_stable_totals_and_complete_rows():
    class Api(reference.LocalApi):
        def __init__(self):
            super().__init__("http://127.0.0.1:8765")

        def get(self, route, params=None):
            page = int(dict(params)["page"])
            return {
                **explorer(), "page": page, "pages": 2, "groups": 2,
                "total": "1000" if page == 1 else "1001",
            }

    with pytest.raises(ValueError, match="changed"):
        Api().explore([], ("service", "pricing_model"))


def test_invalid_summary_date_is_visible_without_replacing_cost_data():
    profile = {"sheets": {"Command Panel": {
        "start_row": 1, "start_column": 1, "values": [[None, None, None], [None, None, "6/31/2026"]],
    }}}
    result = reference.formula_checks(profile, {("2026-05", "account-a"): scope()})
    assert result[0]["cell"] == "Command Panel!C2"
    assert result[0]["status"] == "unavailable"
    assert "invalid" in result[0]["definition"]


def test_recomputed_credit_eligibility_does_not_certify_ytd_or_drawdown():
    sheet = {"start_row": 1, "start_column": 1, "values": [[None] * 17 for _ in range(17)]}
    sheet["values"][10][2] = Decimal(46143)
    sheet["values"][12][1] = "ExampleUS"
    sheet["values"][12][16] = Decimal(100)
    data = scope()
    data.eligible_gbp = Decimal(100)
    result = reference.formula_checks({"sheets": {"Credits": sheet}}, {("2026-05", "account-a"): data})
    assert result == [{
        "cell": "Credits!Q13", "status": "recomputed",
        "definition": "Credit-eligible Cost GBP across available months in the credit table only. Matching a YTD-labelled cell does not establish full-year coverage or actual credit consumption.",
        "reference": Decimal(100), "cached_cell": Decimal(100), "formula": None,
    }]


def test_formula_checks_do_not_guess_enrollment_positions_without_source_labels():
    sheet = {"start_row": 1, "start_column": 1, "values": [[None] * 17 for _ in range(17)]}
    sheet["values"][10][2] = Decimal(46143)
    sheet["values"][12][16] = Decimal(100)
    data = scope()
    data.eligible_gbp = Decimal(100)
    assert reference.formula_checks({"sheets": {"Credits": sheet}}, {("2026-05", "account-a"): data}) == []


def test_persistent_reference_preserves_dashboard_and_records_default_unverified(tmp_path, monkeypatch):
    extraction = tmp_path / "extract"
    extraction.mkdir()
    output = tmp_path / "output"
    dashboard = tmp_path / "existing.html"
    primary = snapshot()
    dashboard.write_text('<script type="application/json" id="mbr-data">' + json.dumps(primary) + "</script>", encoding="utf-8")
    original = dashboard.read_bytes()
    profile = {
        "source_name": "Fictional_Source.xlsb", "source_sha256": "a" * 64,
        "extracted_utc": "2026-11-10T00:00:00+00:00", "data_rows": 1,
        "sheets": {"Trend": {
            "start_row": 1, "start_column": 1,
            "values": [["Based on Amortized Cost"], ["Currency: GBP"]],
        }},
    }
    (extraction / "mbr-profile.json").write_text(json.dumps(profile), encoding="utf-8")
    row = {field: "" for field in mbr.SOURCE_FIELDS}
    row.update({
        "BillingPeriodStartDate": "46143", "BillingAccountId": "account-a", "Cost": "1000",
        "Cost GBP": "1000", "Enrolment": "ExampleUS", "MeterCategory": "Storage",
        "PricingModel": "OnDemand", "Usage Type": "Azure", "Sub Type": "Greenfield",
        "Utilization Type": "Used", "IsAzureCreditEligible": "True",
    })
    with (extraction / "mbr-data.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, list(row))
        writer.writeheader()
        writer.writerow(row)

    class Api:
        base = "http://127.0.0.1:8765"

        def get(self, route, params=None):
            if route == "/api/meta":
                return {
                    "recovery_required": False, "billing_account_currencies": {"account-a": ["GBP"]},
                    "scopes": [{"currency": "GBP", "cost_kind": "amortized", "synthetic": False}],
                }
            return [{"period": "2026-05-01", "total": "1000.0000", "complete": True}]

        def explore(self, params, groups):
            return explorer()

    monkeypatch.setattr(reference.mbr, "ROOT", tmp_path)
    report = reference.verify(extraction, dashboard, Api(), None, output)
    assert dashboard.read_bytes() == original
    assert report["default_for_unlisted_metrics"] == "unverified"
    assert report["standalone"]["status"] == "not_comparable"
    assert (output / "Reference-Snapshot.json").is_file()
    assert report["reference"]["normalized_snapshot_sha256"] == hashlib.sha256(
        (output / "Reference-Snapshot.json").read_bytes(),
    ).hexdigest()
    assert (output / "Reference-Verification.html").is_file()
    catalog_path = tmp_path / "data" / "mbr" / "references" / "index.json"
    assert len(json.loads(catalog_path.read_text())["references"]) == 1
    reference.verify(extraction, dashboard, Api(), None, output)
    assert len(json.loads(catalog_path.read_text())["references"]) == 1
    report["reference"]["source_name"] = "<img src=x onerror=alert(1)>"
    report["cost_checks"][0]["total"]["difference"] = Decimal("-0.000001")
    html = reference.render_report(report)
    assert "<img src=x" not in html and "&lt;img" in html
    assert "-0.0000" not in html
    assert "connect-src 'none'" in html and "--cp-accent: #b11f4b;" in html
    assert "not blanket approval" in html
