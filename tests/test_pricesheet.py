"""Price sheet import. Fixtures are invented (same header as the real export, made-up rows).

Sep file: 9 data rows. Valid: mtr-1 (consumption, 1-year and 3-year savings plan), mtr-2 (USD), mtr-3 (per 1000 hours),
mtr-4 (a genuine zero price) = 6. Rejected: blank unit price, impossible date, blank meter id = 3.
"""
from decimal import Decimal as D
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from azfinops.migrations.runner import migrate
from azfinops.pricesheet import PriceSheetError, expand, import_price_sheets

FIX = Path(__file__).parent / "fixtures" / "price_sheet"
SEP, AUG, BAD = FIX / "invented_price_sheet_sep.csv", FIX / "invented_price_sheet_aug.csv", FIX / "missing_columns.csv"


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "t.duckdb"
    migrate(p)
    return p


def rows(db, sql, params=None):
    c = duckdb.connect(str(db), read_only=True)
    try:
        return c.execute(sql, params or []).fetchall()
    finally:
        c.close()


def test_loads_valid_rows_and_rejects_bad_ones_with_reasons(db):
    r = import_price_sheets(db, [SEP])
    assert (r.rows_loaded, r.rows_rejected) == (6, 3)
    assert r.periods == ["2026-09-01"] and r.by_currency == {"GBP": 5, "USD": 1}
    assert r.by_price_type == {"Consumption": 4, "SavingsPlan": 2}
    assert set(r.reject_causes) == {"unit price missing or not a number", "effective start missing or not a date",
                                    "meter id missing"}
    assert rows(db, "SELECT count(*) FROM price_sheet") == [(6,)]
    assert rows(db, "SELECT rows_loaded, rows_rejected FROM price_sheet_files") == [(6, 3)]


def test_values_are_exact_and_blank_is_unknown_not_zero(db):
    import_price_sheets(db, [SEP])
    sp = rows(db, "SELECT unit_price, base_price, market_price, term, offer_id FROM price_sheet "
                  "WHERE meter_id='mtr-1' AND price_type='SavingsPlan' AND term='P1Y'")
    assert sp == [(D("0.08"), None, D("0.0885"), "P1Y", "OFFER-A")]          # blank BasePrice stays NULL
    free = rows(db, "SELECT unit_price, base_price, market_price, included_quantity FROM price_sheet WHERE meter_id='mtr-4'")
    assert free == [(D(0), None, D(0), D(5))]                          # a real zero price is kept as zero
    block = rows(db, "SELECT unit_of_measure, unit_price FROM price_sheet WHERE meter_id='mtr-3'")
    assert block == [("1000 Hours", D("36.22"))]                             # per-block unit is kept, not divided
    assert rows(db, "SELECT currency, meter_region FROM price_sheet WHERE meter_id='mtr-2'") == [("USD", "")]


def test_reimport_replaces_the_period_and_other_periods_are_untouched(db):
    import_price_sheets(db, [SEP, AUG])
    assert rows(db, "SELECT count(*) FROM price_sheet") == [(7,)]
    r = import_price_sheets(db, [SEP])
    assert r.replaced_rows == 6
    assert rows(db, "SELECT count(*) FROM price_sheet") == [(7,)]
    assert rows(db, "SELECT effective_start, unit_price FROM price_sheet WHERE meter_id='mtr-1' AND price_type='Consumption' "
                    "ORDER BY 1") == [(rows(db, "SELECT DATE '2026-08-01'")[0][0], D("0.09")),
                                      (rows(db, "SELECT DATE '2026-09-01'")[0][0], D("0.10"))]


def test_missing_required_column_or_file_is_refused_and_nothing_is_loaded(db, tmp_path):
    with pytest.raises(PriceSheetError, match="missing required column"):
        import_price_sheets(db, [BAD])
    with pytest.raises(PriceSheetError, match="not found"):
        import_price_sheets(db, [tmp_path / "nope.csv"])
    assert rows(db, "SELECT count(*) FROM price_sheet") == [(0,)]


def test_wildcards_are_expanded_by_the_program_and_an_empty_match_is_an_error(tmp_path):
    for n in ("a_1.csv", "a_0.csv", "b_0.csv"):
        (tmp_path / n).write_text("x")
    assert [p.name for p in expand([str(tmp_path / "a_*.csv")])] == ["a_0.csv", "a_1.csv"]
    assert [p.name for p in expand([str(tmp_path / "b_0.csv")])] == ["b_0.csv"]
    with pytest.raises(PriceSheetError, match="no files match"):
        expand([str(tmp_path / "zzz_*.csv")])
