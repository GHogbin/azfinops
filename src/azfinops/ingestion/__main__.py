import argparse
from pathlib import Path
from typing import Any

import duckdb

from ..aggregates import affected_months, rebuild
from ..config import Settings
from ..migrations.runner import migrate
from .importer import ImportRejected, import_files


def main() -> None:
    ap = argparse.ArgumentParser(description="Import Azure cost CSV file(s) as one batch")
    ap.add_argument("files", nargs="+", type=Path, help="all partition files of ONE export")
    ap.add_argument("--kind", required=True, choices=["actual", "amortized"])
    ap.add_argument("--db", type=Path, help="default: <AZFINOPS_DATA_DIR>\\azfinops.duckdb")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--supersede", action="store_true")
    ap.add_argument("--reimport", action="store_true",
                    help="with --supersede: allow a file already imported (e.g. after a mapping change)")
    ap.add_argument("--max-reject-ratio", type=float, default=0.001)
    a = ap.parse_args()
    s = Settings.from_env()
    db = a.db or s.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    migrate(db)

    def show(p: dict[str, Any]) -> None:
        if p.get("phase") == "retention":
            print("\n  applying portfolio-wide 12-month retention...", flush=True)
            return
        print(f"\r  {p['file']}: {p['rows_loaded']:,} loaded, {p['rows_rejected']:,} rejected", end="", flush=True)

    try:
        r = import_files(db, a.files, a.kind, synthetic=a.synthetic, supersede=a.supersede,
                         max_reject_ratio=a.max_reject_ratio, settings=s, progress=show,
                         reimport=a.reimport)
    except ImportRejected as e:
        print(f"\nIMPORT REFUSED: {e}")
        raise SystemExit(2) from e
    print(f"\nbatch {r.batch_id}: {r.rows_loaded:,} rows, {r.rows_rejected:,} rejected, "
          f"{r.period_start}..{r.period_end}, {r.elapsed_s:.1f}s")
    if r.retention:
        print(f"  {r.retention.describe()} purged; {r.rows_retained:,} rows from this batch remain")
    con = duckdb.connect(str(db), read_only=True)
    try:
        months = affected_months(con, [r.batch_id, *r.superseded_batches])
    finally:
        con.close()
    done = rebuild(db, s, months)
    print(f"  aggregates rebuilt for: {', '.join(x.isoformat()[:7] for x in done) or 'nothing'}")
    try:
        from ..dimensions import harvest_and_store
        n, multi = harvest_and_store(db, a.files)
        print(f"  subscription names stored: {n}" + (f" ({multi} ids with several names)" if multi else ""))
    except (OSError, ValueError, duckdb.Error) as e:
        print(f"  warning: subscription names not stored ({type(e).__name__}: {e}); run azfinops-names")
    try:
        from ..accounts import harvest_and_store as store_accounts
        names = store_accounts(s.data_dir, a.files)
        print(f"  enrollment names stored: {len(names)}")
    except (OSError, ValueError, duckdb.Error) as e:
        print(f"  warning: enrollment names not stored ({type(e).__name__}: {e})")
    for k, v in r.warnings.items():
        print(f"  warning {k}: {v:,}")
    if r.unmapped_columns:
        print(f"  ignored columns: {', '.join(r.unmapped_columns)}")


if __name__ == "__main__":
    main()
