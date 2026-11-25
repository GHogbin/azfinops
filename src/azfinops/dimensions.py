"""Display-name dimensions harvested from export CSVs (currently: subscription names).

Only the needed columns are read from the file (a 4.7 GB export is tokenised once, but nothing else
is materialised). Names never affect cost figures; they are labels for ids.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv

from .config import Settings

_ID_HEADERS = ("subscriptionid", "subscriptionguid")
_NAME_HEADERS = ("subscriptionname",)
BLOCK_BYTES = 64 * 1024 * 1024


def _pick(header: list[str], wanted: tuple[str, ...]) -> str | None:
    for h in header:
        if h.strip().lower() in wanted:
            return h
    return None


def harvest_subscriptions(csv_path: Path) -> dict[str, Counter[str]]:
    """{subscription_id: Counter(name -> rows)} from one export file. Blank names are ignored."""
    with open(csv_path, encoding="utf-8-sig") as f:
        header = [h.strip() for h in f.readline().rstrip("\r\n").split(",")]
    id_col, name_col = _pick(header, _ID_HEADERS), _pick(header, _NAME_HEADERS)
    if id_col is None or name_col is None:
        return {}
    conv = pacsv.ConvertOptions(include_columns=[id_col, name_col],
                                column_types={id_col: pa.string(), name_col: pa.string()},
                                null_values=[""], strings_can_be_null=True)
    out: dict[str, Counter[str]] = {}
    reader = pacsv.open_csv(csv_path, read_options=pacsv.ReadOptions(block_size=BLOCK_BYTES),
                            convert_options=conv)
    for batch in reader:
        t = pa.Table.from_batches([batch]).rename_columns(["i", "n"])
        g = t.group_by(["i", "n"]).aggregate([([], "count_all")])
        for i, n, c in zip(g["i"].to_pylist(), g["n"].to_pylist(), g["count_all"].to_pylist(), strict=True):
            if i and n and i.strip() and n.strip():
                out.setdefault(i.strip().lower(), Counter())[n.strip()] += int(c)
    return out


def store_subscriptions(db_path: Path, found: dict[str, Counter[str]]) -> int:
    """Upsert names; the most frequent name for an id wins. Returns rows written."""
    if not found:
        return 0
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [(sid, c.most_common(1)[0][0], len(c), now) for sid, c in sorted(found.items())]
    con = duckdb.connect(str(db_path))
    try:
        con.execute("BEGIN")
        con.executemany("INSERT OR REPLACE INTO dim_subscription VALUES (?, ?, ?, ?)", rows)
        con.execute("COMMIT")
    finally:
        con.close()
    return len(rows)


def harvest_and_store(db_path: Path, csv_paths: list[Path]) -> tuple[int, int]:
    """Returns (subscriptions stored, ids that had more than one name)."""
    merged: dict[str, Counter[str]] = {}
    for p in csv_paths:
        for sid, c in harvest_subscriptions(p).items():
            merged.setdefault(sid, Counter()).update(c)
    return store_subscriptions(db_path, merged), sum(1 for c in merged.values() if len(c) > 1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Store subscription display names from export CSV file(s)")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--db", type=Path)
    a = ap.parse_args()
    s = Settings.from_env()
    db = a.db or s.db_path
    from .migrations.runner import migrate
    applied = migrate(db)
    if applied:
        print(f"applied migrations: {applied}")
    n, multi = harvest_and_store(db, a.files)
    print(f"stored {n} subscription names" + (f"; {multi} ids had more than one name (most frequent kept)"
                                               if multi else ""))


if __name__ == "__main__":
    main()
