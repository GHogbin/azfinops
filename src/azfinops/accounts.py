"""Enrollment (billing account) display names, read from the BillingAccountName column of the export.

The name is the same on every row of an account, so only the first block of each file is read (a 20 GB file takes
about a second). Names are labels only and never affect a cost figure. They are kept in a small JSON file next to the
database rather than in it, so saving one never needs the database writer.

Usage:  uv run python -m azfinops.accounts <export csv> [<export csv> ...]
        uv run python -m azfinops.accounts --set example-account="Example enrollment"
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv

from .config import Settings

BLOCK_BYTES = 8 * 1024 * 1024


def _path(data_dir: Path) -> Path:
    return data_dir / "billing_account_names.json"


def load(data_dir: Path) -> dict[str, str]:
    try:
        raw = json.loads(_path(data_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(raw, dict) and str(v).strip()}


def harvest(csv_path: Path) -> dict[str, str]:
    """{billing account id: most common non-blank name} from the first block of one export file."""
    with open(csv_path, encoding="utf-8-sig") as f:
        header = [h.strip() for h in f.readline().rstrip("\r\n").split(",")]
    low = {h.lower(): h for h in header}
    id_col, name_col = low.get("billingaccountid"), low.get("billingaccountname")
    if id_col is None or name_col is None:
        return {}
    reader = pacsv.open_csv(
        csv_path, read_options=pacsv.ReadOptions(block_size=BLOCK_BYTES),
        convert_options=pacsv.ConvertOptions(include_columns=[id_col, name_col],
                                             column_types={id_col: pa.string(), name_col: pa.string()},
                                             null_values=[""], strings_can_be_null=True))
    try:
        batch = reader.read_next_batch()
    except StopIteration:
        return {}
    seen: dict[str, Counter[str]] = {}
    for i, n in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist(), strict=True):
        if i and n and i.strip() and n.strip():
            seen.setdefault(i.strip(), Counter())[n.strip()] += 1
    return {i: c.most_common(1)[0][0] for i, c in seen.items()}


def store(data_dir: Path, names: dict[str, str]) -> dict[str, str]:
    """Merge names into the file (new values win) and return the full set."""
    merged = {**load(data_dir), **{k: v.strip() for k, v in names.items() if v and v.strip()}}
    data_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=data_dir, prefix="billing_account_names.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=1, sort_keys=True)
        os.replace(tmp, _path(data_dir))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return merged


def harvest_and_store(data_dir: Path, csv_paths: list[Path]) -> dict[str, str]:
    found: dict[str, str] = {}
    for p in csv_paths:
        found.update(harvest(p))
    return store(data_dir, found) if found else load(data_dir)


def main() -> None:
    ap = argparse.ArgumentParser(description="Store enrollment display names")
    ap.add_argument("files", nargs="*", type=Path, help="export CSV files to read names from")
    ap.add_argument("--set", action="append", default=[], metavar='ID="Name"', help="set a name by hand")
    a = ap.parse_args()
    d = Settings.from_env().data_dir
    manual = dict(x.split("=", 1) for x in a.set if "=" in x)
    names = harvest_and_store(d, a.files)
    if manual:
        names = store(d, manual)
    for k, v in sorted(names.items()):
        print(f"{k}: {v}")
    if not names:
        print("no names found (the files have no BillingAccountName values); use --set ID=\"Name\"")


if __name__ == "__main__":
    main()
