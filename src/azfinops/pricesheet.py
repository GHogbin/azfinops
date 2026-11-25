"""EA price sheet importer (reference data, separate from cost facts).

Every column is read as text and typed in SQL, so a bad row is rejected with a reason instead of
becoming a zero. Rows are staged first; only when all files have loaded are the affected periods
replaced, in one transaction. Prices are per the sheet's UnitOfMeasure and are not converted.
"""
from __future__ import annotations

import argparse
import glob
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv

from .config import Settings

BLOCK_BYTES = 64 * 1024 * 1024
REQUIRED = ("EnrollmentNumber", "MeterId", "EffectiveStartDate", "UnitPrice", "CurrencyCode", "PriceType")
OPTIONAL = ("EffectiveEndDate", "MeterName", "MeterType", "MeterCategory", "MeterSubCategory", "ServiceFamily",
            "Product", "SkuID", "ProductID", "PartNumber", "MeterRegion", "UnitOfMeasure", "OfferId", "Term",
            "BasePrice", "MarketPrice", "IncludedQuantity")


class PriceSheetError(Exception):
    """The price sheet could not be imported; nothing from it is visible."""


@dataclass
class PriceSheetResult:
    files: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    periods: list[str] = field(default_factory=list)
    by_currency: dict[str, int] = field(default_factory=dict)
    by_price_type: dict[str, int] = field(default_factory=dict)
    reject_causes: dict[str, int] = field(default_factory=dict)
    replaced_rows: int = 0


def _names(path: Path) -> list[str]:
    return list(pacsv.open_csv(path).schema.names)


def _typed_select(actual: dict[str, str], src: str) -> str:
    def raw(key: str) -> str:
        n = actual.get(key.lower())
        return f'CAST("{n}" AS VARCHAR)' if n else "CAST(NULL AS VARCHAR)"

    def txt(key: str) -> str:
        return f"nullif(trim({raw(key)}), '')"

    def dec(key: str) -> str:
        return f"TRY_CAST({txt(key)} AS DECIMAL(28,10))"

    def day(key: str) -> str:
        t = txt(key)
        return (f"CAST(coalesce(try_strptime({t}, '%m/%d/%Y %H:%M:%S'), try_strptime({t}, '%m/%d/%Y'), "
                f"try_strptime({t}, '%Y-%m-%d')) AS DATE)")

    return (
        f"SELECT {txt('EnrollmentNumber')} AS enrollment, {day('EffectiveStartDate')} AS effective_start, "
        f"{day('EffectiveEndDate')} AS effective_end, {txt('MeterId')} AS meter_id, {txt('MeterName')} AS meter_name, "
        f"{txt('MeterType')} AS meter_type, {txt('MeterCategory')} AS meter_category, "
        f"{txt('MeterSubCategory')} AS meter_subcategory, {txt('ServiceFamily')} AS service_family, "
        f"{txt('Product')} AS product, {txt('SkuID')} AS sku_id, {txt('ProductID')} AS product_id, "
        f"{txt('PartNumber')} AS part_number, coalesce({txt('MeterRegion')}, '') AS meter_region, "
        f"coalesce({txt('UnitOfMeasure')}, '') AS unit_of_measure, coalesce({txt('OfferId')}, '') AS offer_id, "
        f"{txt('PriceType')} AS price_type, coalesce({txt('Term')}, '') AS term, "
        f"upper({txt('CurrencyCode')}) AS currency, {dec('UnitPrice')} AS unit_price, "
        f"{dec('BasePrice')} AS base_price, {dec('MarketPrice')} AS market_price, "
        f"{dec('IncludedQuantity')} AS included_quantity FROM {src}")


_VALID = ("enrollment IS NOT NULL AND effective_start IS NOT NULL AND meter_id IS NOT NULL AND "
          "price_type IS NOT NULL AND currency IS NOT NULL AND unit_price IS NOT NULL")
_COLS = ("enrollment, effective_start, effective_end, meter_id, meter_name, meter_type, meter_category, "
         "meter_subcategory, service_family, product, sku_id, product_id, part_number, meter_region, "
         "unit_of_measure, offer_id, price_type, term, currency, unit_price, base_price, market_price, "
         "included_quantity")


def import_price_sheets(db_path: Path, csv_paths: list[Path],
                       progress: Callable[[dict[str, Any]], None] | None = None) -> PriceSheetResult:
    if not csv_paths:
        raise PriceSheetError("no files given")
    for p in csv_paths:
        if not p.is_file():
            raise PriceSheetError(f"file not found: {p}")
    res = PriceSheetResult(files=len(csv_paths))
    con = duckdb.connect(str(db_path))
    try:
        con.execute(f"CREATE TEMP TABLE ps_stage AS SELECT {_COLS}, CAST('' AS VARCHAR) AS source_file "
                    f"FROM price_sheet WHERE false")
        log: list[tuple[str, int, int, int]] = []
        for path in csv_paths:
            names = _names(path)
            actual = {n.lstrip("﻿").strip().lower(): n for n in names}
            missing = [c for c in REQUIRED if c.lower() not in actual]
            if missing:
                raise PriceSheetError(f"{path.name}: missing required column(s) {missing}; found {names[:12]}")
            conv = pacsv.ConvertOptions(column_types={n: pa.string() for n in names}, null_values=[""],
                                        strings_can_be_null=False)
            reader = pacsv.open_csv(path, read_options=pacsv.ReadOptions(block_size=BLOCK_BYTES), convert_options=conv)
            loaded = rejected = 0
            for batch in reader:
                n_in = batch.num_rows
                if n_in == 0:
                    continue
                con.register("blk", pa.Table.from_batches([batch]))
                try:
                    typed = _typed_select(actual, "blk")
                    before = con.execute("SELECT count(*) FROM ps_stage").fetchall()[0][0]
                    con.execute(f"INSERT INTO ps_stage SELECT {_COLS}, CAST(? AS VARCHAR) FROM ({typed}) WHERE {_VALID}", [path.name])
                    n_ok = con.execute("SELECT count(*) FROM ps_stage").fetchall()[0][0] - before
                    if n_ok < n_in:
                        c = con.execute(
                            f"SELECT count(*) FILTER (WHERE unit_price IS NULL), "
                            f"count(*) FILTER (WHERE effective_start IS NULL), "
                            f"count(*) FILTER (WHERE meter_id IS NULL), "
                            f"count(*) FILTER (WHERE price_type IS NULL OR currency IS NULL OR enrollment IS NULL) "
                            f"FROM ({typed})").fetchall()[0]
                        for label, v in zip(("unit price missing or not a number", "effective start missing or not a date",
                                             "meter id missing", "price type, currency or enrollment missing"), c):
                            if v:
                                res.reject_causes[label] = res.reject_causes.get(label, 0) + int(v)
                finally:
                    con.unregister("blk")
                loaded += n_ok
                rejected += n_in - n_ok
                if progress:
                    progress({"file": path.name, "loaded": loaded, "rejected": rejected})
            log.append((path.name, path.stat().st_size, loaded, rejected))
            res.rows_loaded += loaded
            res.rows_rejected += rejected
        if res.rows_loaded == 0:
            raise PriceSheetError("no valid rows were found; nothing was imported")
        con.execute("BEGIN")
        try:
            res.replaced_rows = int(con.execute(
                "SELECT count(*) FROM price_sheet WHERE (enrollment, effective_start) IN "
                "(SELECT DISTINCT enrollment, effective_start FROM ps_stage)").fetchall()[0][0])
            con.execute("DELETE FROM price_sheet WHERE (enrollment, effective_start) IN "
                        "(SELECT DISTINCT enrollment, effective_start FROM ps_stage)")
            con.execute("INSERT INTO price_sheet SELECT * FROM ps_stage")
            now = datetime.now(UTC).replace(tzinfo=None)
            for name, size, ok, bad in log:
                con.execute("DELETE FROM price_sheet_files WHERE file_name = ?", [name])
                con.execute("INSERT INTO price_sheet_files VALUES (?, ?, ?, ?, ?)", [name, now, size, ok, bad])
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        res.periods = [str(r[0]) for r in con.execute(
            "SELECT DISTINCT effective_start FROM ps_stage ORDER BY 1").fetchall()]
        res.by_currency = {r[0]: int(r[1]) for r in con.execute(
            "SELECT currency, count(*) FROM ps_stage GROUP BY 1 ORDER BY 2 DESC").fetchall()}
        res.by_price_type = {r[0]: int(r[1]) for r in con.execute(
            "SELECT price_type, count(*) FROM ps_stage GROUP BY 1 ORDER BY 2 DESC").fetchall()}
        return res
    finally:
        con.close()


def expand(patterns: list[str]) -> list[Path]:
    """Expand wildcards ourselves: PowerShell passes `*` through to the program unexpanded."""
    out: list[Path] = []
    for pat in patterns:
        hits = sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat]
        if not hits:
            raise PriceSheetError(f"no files match {pat}")
        out.extend(Path(h) for h in hits)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Import EA price sheet CSV file(s); a period already loaded is replaced")
    ap.add_argument("files", nargs="+", help="CSV file(s); wildcards such as samples\\ArmPriceSheet_*.csv are allowed")
    ap.add_argument("--db", type=Path)
    a = ap.parse_args()
    s = Settings.from_env()
    db = a.db or s.db_path
    from .migrations.runner import migrate
    applied = migrate(db)
    if applied:
        print(f"applied migrations: {applied}")
    try:
        r = import_price_sheets(db, expand(a.files), progress=lambda p: print(f"\r  {p['file']}: {p['loaded']:,} rows", end=""))
    except PriceSheetError as e:
        raise SystemExit(f"price sheet import failed: {e}")
    print()
    print(f"price sheet: {r.rows_loaded:,} rows loaded from {r.files} file(s), {r.rows_rejected:,} rejected")
    print(f"  periods: {', '.join(r.periods)}" + (f"; replaced {r.replaced_rows:,} existing rows" if r.replaced_rows else ""))
    print(f"  by currency: {r.by_currency}")
    print(f"  by price type: {r.by_price_type}")
    for k, v in r.reject_causes.items():
        print(f"  rejected: {v:,} rows, {k}")


if __name__ == "__main__":
    main()
