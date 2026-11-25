"""Needs duckdb + pyarrow (first executed on the author's Windows PC, not in the build sandbox)."""
from decimal import Decimal as D
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from azfinops.ingestion.importer import ImportRejected, import_files
from azfinops.migrations.runner import migrate

FIX = Path(__file__).parent / "fixtures" / "ea_amortized"
MAIN, PART2, MINIMAL, RESUB = (FIX / n for n in
                               ("ea_amortized_sep.csv", "second_partition.csv", "minimal.csv",
                                "overlap_resubmission.csv"))


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "t.duckdb"
    migrate(p)
    return p


def q(db, sql, *args):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql, list(args)).fetchall()
    finally:
        con.close()


def test_real_format_import_counts_and_total(db):
    r = import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    assert (r.rows_loaded, r.rows_rejected) == (9, 2)
    assert (str(r.period_start), str(r.period_end)) == ("2026-09-02", "2026-09-02")
    assert q(db, "SELECT sum(cost) FROM v_cost_facts")[0][0] == D("16.25")  # hand-computed in docs
    assert "BillingAccountName" not in r.unmapped_columns and "BillingProfileName" in r.unmapped_columns


def test_identical_billing_rows_are_both_kept(db):
    import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    assert q(db, "SELECT count(*) FROM v_cost_facts WHERE meter_id = 'm-a'")[0][0] == 2


def test_rejected_rows_have_actionable_validation_errors(db):
    import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    rows = q(db, "SELECT code, row_number, column_name, sample_value FROM validation_results "
                 "WHERE severity = 'error' ORDER BY row_number")
    assert rows == [("invalid_date", 10, "date", "13/45/2026"), ("invalid_cost", 11, "cost", "abc")]


def test_normalisation_rules(db):
    import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    pm = dict(q(db, "SELECT meter_id, pricing_model FROM v_cost_facts WHERE charge_type = 'Usage'"))
    assert pm["m-a"] == "On Demand" and pm["m-ri"] == "Reservation" and pm["m-sp"] == "SavingsPlan"
    ri = q(db, "SELECT payg_price, payg_basis FROM v_cost_facts WHERE meter_id = 'm-ri' AND charge_type = 'Usage'")[0]
    assert ri == (D("0.8"), "contract")                       # PayGPrice 0 on reservation row is not "free"
    sp = q(db, "SELECT payg_price, payg_basis FROM v_cost_facts WHERE meter_id = 'm-sp'")[0]
    assert sp == (D("0.15"), "payg")
    free = q(db, "SELECT payg_price, payg_basis FROM v_cost_facts WHERE meter_id = 'm-free'")[0]
    assert free == (D("0.067"), "payg")
    unused = q(db, "SELECT payg_price, payg_basis FROM v_cost_facts WHERE charge_type = 'UnusedReservation'")
    assert unused == [(None, None), (None, None)]             # unknown, never 0
    assert q(db, "SELECT unit FROM v_cost_facts WHERE meter_id = 'm-mkt'")[0][0] is None  # blank unit stays NULL
    assert q(db, "SELECT json_extract_string(tags, '$.app') FROM v_cost_facts WHERE meter_id = 'm-st'")[0][0] == "x"
    assert str(q(db, "SELECT usage_date FROM v_cost_facts WHERE meter_id = 'm-a'")[0][0]) == "2026-09-02"
    assert q(db, "SELECT subscription_id FROM v_cost_facts LIMIT 1")[0][0].islower()


def test_utilization_inputs_are_loaded(db):
    import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    used = q(db, "SELECT sum(quantity) FROM v_cost_facts WHERE pricing_model = 'Reservation' AND charge_type = 'Usage'")
    unused = q(db, "SELECT sum(quantity) FROM v_cost_facts WHERE charge_type = 'UnusedReservation'")
    assert (used[0][0], unused[0][0]) == (D(4), D(2))     # 4 / (4 + 2) = 66.7%, from the fixture


def test_warnings_recorded(db):
    r = import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    assert r.warnings == {"contract_basis_payg": 1}


def test_reject_ratio_threshold_fails_whole_batch_and_hides_rows(db):
    with pytest.raises(ImportRejected, match="invalid"):
        import_files(db, [MAIN], "amortized", max_reject_ratio=0.01)
    assert q(db, "SELECT count(*) FROM cost_facts")[0][0] == 0
    assert q(db, "SELECT status FROM import_batches")[0][0] == "failed"


def test_reimport_of_identical_file_is_rejected(db):
    import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    with pytest.raises(ImportRejected, match="already imported"):
        import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    assert q(db, "SELECT count(*) FROM v_cost_facts")[0][0] == 9


def test_partitions_form_one_batch(db):
    r = import_files(db, [MAIN, PART2], "amortized", max_reject_ratio=1.0)
    assert r.files == 2 and r.rows_loaded == 10
    assert q(db, "SELECT count(DISTINCT batch_id), count(DISTINCT file_id) FROM cost_facts")[0] == (1, 2)


def test_same_file_twice_in_one_call_is_rejected(db):
    with pytest.raises(ImportRejected, match="twice"):
        import_files(db, [MAIN, MAIN], "amortized", max_reject_ratio=1.0)


def test_overlap_needs_explicit_supersede_and_is_atomic(db):
    import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    with pytest.raises(ImportRejected, match="overlaps"):
        import_files(db, [RESUB], "amortized")
    assert q(db, "SELECT sum(cost) FROM v_cost_facts")[0][0] == D("16.25")   # old data untouched
    r = import_files(db, [RESUB], "amortized", supersede=True)
    assert r.superseded_batches == [1]
    assert q(db, "SELECT sum(cost) FROM v_cost_facts")[0][0] == D(9)        # old batch now invisible


def test_actual_and_amortized_of_same_period_do_not_conflict_or_mix(db):
    import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    import_files(db, [RESUB], "actual")                      # different cost kind: allowed
    kinds = dict(q(db, "SELECT cost_kind, sum(cost) FROM v_cost_facts GROUP BY 1"))
    assert kinds == {"amortized": D("16.25"), "actual": D(9)}


def test_minimal_required_columns_and_iso_dates(db):
    r = import_files(db, [MINIMAL], "actual")
    assert r.rows_loaded == 1
    assert q(db, "SELECT usage_date, cost, billing_currency, payg_price FROM v_cost_facts")[0] == (
        __import__("datetime").date(2026, 10, 3), D("7.5"), "USD", None)


def test_path_outside_allowed_dirs_is_refused(db, tmp_path):
    allowed = tmp_path / "imports"
    allowed.mkdir()
    with pytest.raises(ImportRejected, match="allowed import folder"):
        import_files(db, [MAIN], "amortized", allowed_dirs=(allowed,))


def test_non_csv_and_missing_files_are_refused(db, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("a")
    with pytest.raises(ImportRejected, match="only .csv"):
        import_files(db, [f], "amortized")
    with pytest.raises(FileNotFoundError):
        import_files(db, [tmp_path / "nope.csv"], "amortized")


def test_focus_and_missing_columns_leave_nothing_behind(db, tmp_path):
    f = tmp_path / "focus.csv"
    f.write_text("BilledCost,ChargeCategory,Date\n1,Usage,2026-01-01\n")
    with pytest.raises(ImportRejected, match="FOCUS"):
        import_files(db, [f], "amortized")
    assert q(db, "SELECT count(*) FROM cost_facts")[0][0] == 0


def test_interrupted_batch_is_cleaned_up_on_next_start(db):
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO import_batches (batch_id, source_kind, cost_kind, is_synthetic, status, created_at) "
                "VALUES (99, 'csv', 'actual', false, 'running', now())")
    con.close()
    import_files(db, [MINIMAL], "actual")
    assert q(db, "SELECT status FROM import_batches WHERE batch_id = 99")[0][0] == "failed"


def test_invalid_cost_kind_is_refused(db):
    with pytest.raises(ImportRejected, match="cost_kind"):
        import_files(db, [MAIN], "both")


def test_zero_cost_purchase_rows_in_amortized_export_are_not_flagged(db, tmp_path):
    """Observed in a real EA amortized export: Purchase rows exist with cost 0."""
    hdr = "BillingAccountId,Date,Cost,BillingCurrency,ChargeType\r\n"
    f = tmp_path / "zero.csv"
    f.write_text(hdr + "A,09/01/2026,0,GBP,Purchase\r\nA,09/01/2026,1,GBP,Usage\r\n")
    assert import_files(db, [f], "amortized").warnings == {}
    g = tmp_path / "nonzero.csv"
    g.write_text(hdr + "A,10/01/2026,5,GBP,Purchase\r\nA,10/01/2026,1,GBP,Usage\r\n")
    assert import_files(db, [g], "amortized").warnings == {"kind_mismatch": 1}


def test_new_columns_are_stored(db):
    import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    assert q(db, "SELECT count(*) FROM v_cost_facts WHERE billing_account_name = 'Invented account'")[0][0] > 0
    assert q(db, "SELECT count(*) FROM v_cost_facts WHERE effective_price IS NOT NULL")[0][0] > 0


def test_without_payg_price_the_contract_unit_price_is_the_baseline(db, monkeypatch):
    monkeypatch.setenv("AZFINOPS_USE_PAYG_PRICE", "0")
    r = import_files(db, [MAIN], "amortized", max_reject_ratio=1.0)
    assert "contract_basis_payg" not in r.warnings
    bad = q(db, "SELECT count(*) FROM v_cost_facts WHERE payg_price IS DISTINCT FROM unit_price "
                "AND unit_price > 0")[0][0]
    assert bad == 0
    assert q(db, "SELECT count(*) FROM v_cost_facts WHERE payg_basis = 'payg'")[0][0] == 0


@pytest.mark.parametrize("old_rows,new_rows", [
    ("A,2026-08-31,10,GBP,Usage\nA,2026-09-30,20,GBP,Usage\n", "A,2026-09-30,7,GBP,Usage\n"),
    ("A,2026-09-01,10,GBP,Usage\nB,2026-09-01,20,GBP,Usage\n", "A,2026-09-01,7,GBP,Usage\n"),
    ("A,2026-09-01,10,GBP,Usage\nA,2026-09-01,20,USD,Usage\n", "A,2026-09-01,7,GBP,Usage\n"),
    ("A,2026-09-01,10,GBP,Usage\nA,2026-09-02,20,GBP,Usage\nA,2026-09-03,30,GBP,Usage\n",
     "A,2026-09-01,7,GBP,Usage\nA,2026-09-03,8,GBP,Usage\n"),
])
def test_narrower_supersede_is_refused_without_hiding_old_data(db, tmp_path, old_rows, new_rows):
    header = "BillingAccountId,Date,Cost,BillingCurrency,ChargeType\n"
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    old.write_text(header + old_rows)
    new.write_text(header + new_rows)
    original = import_files(db, [old], "amortized")
    before = q(db, "SELECT billing_account_id, billing_currency, usage_date, cost FROM v_cost_facts ORDER BY ALL")
    with pytest.raises(ImportRejected, match="narrower"):
        import_files(db, [new], "amortized", supersede=True)
    assert q(db, "SELECT billing_account_id, billing_currency, usage_date, cost FROM v_cost_facts ORDER BY ALL") == before
    assert q(db, "SELECT status FROM import_batches WHERE batch_id = ?", original.batch_id) == [("committed",)]


def test_another_accounts_later_dates_do_not_create_a_false_overlap(db, tmp_path):
    header = "BillingAccountId,Date,Cost,BillingCurrency,ChargeType\n"
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    old.write_text(header + "A,2026-08-31,10,GBP,Usage\nB,2026-09-30,20,GBP,Usage\n")
    new.write_text(header + "A,2026-09-30,30,GBP,Usage\n")
    import_files(db, [old], "amortized")
    result = import_files(db, [new], "amortized")
    assert result.superseded_batches == []
    assert q(db, "SELECT sum(cost), count(*) FROM v_cost_facts") == [(D(60), 3)]
