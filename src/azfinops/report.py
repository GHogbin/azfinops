"""Plain-text overview for reconciling the imported data against the Azure portal."""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from . import queries as q
from .canonical import COST_KINDS
from .config import Settings


def m(x: Decimal | None, places: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{places}f}"


def pct(x: Decimal | None) -> str:
    return "n/a (no data)" if x is None else f"{x * 100:.1f}%"


def render(con: duckdb.DuckDBPyConnection, f: q.Filters, top_n: int = 8) -> str:
    o = q.overview(con, f, top_n)
    t, qual, c = o["totals"], o["quality"], o["commitments"]
    out = [f"=== {f.cost_kind.upper()} cost, {f.currency} (native currency, no conversion) ==="]
    if qual["synthetic"]:
        out.append("*** SYNTHETIC DATA ***")
    if qual["has_incomplete_month"]:
        out.append("WARNING: at least one month in range does not cover every day; its totals are partial.")
    for mo in qual["months"]:
        built = mo["built_at"].strftime("%Y-%m-%d %H:%M") if mo["built_at"] else "not built"
        out.append(f"  {mo['month']:%Y-%m}: {mo['rows']:,} rows, complete={mo['complete']}, built {built}")
    out += ["", f"TOTAL {m(t['total'])}   (usage {m(t['usage'])})   rows {t['rows']:,}", "By charge type:"]
    out += [f"  {k:<20}{m(v):>18}" for k, v in t["by_charge_type"].items()]
    for dim, label in (("subscription", "subscription id"), ("service", "service (meter category)"),
                       ("resource_group", "resource group"), ("application", "application")):
        r = o["top"][dim]
        out += ["", f"Top {len(r['rows'])} by {label} of {r['groups']:,} groups:"]
        out += [f"  {str(x['key'])[:48]:<50}{m(x['cost']):>16}" for x in r["rows"]]
        out.append(f"  {'(all others)':<50}{m(r['other']):>16}")
    out += ["", f"Attributed to an application tag: {pct(qual['attributed_share'])} of cost"]
    out.append("")
    if not c["available"]:
        out.append(f"Commitments: not available. {c['reason']}")
    else:
        u, cv, d = c["utilization"], c["coverage"], c["observed_discount"]
        out += ["Commitments (observed):",
                (f"  reservation utilization, cost-weighted {pct(u['ratio_cost'])}  "
                 f"(used {m(u['used_cost'])}, unused {m(u['unused_cost'])})"),
                (f"    hours blend {pct(u['ratio'])} (used {m(u['used_hours'])} h, unused {m(u['unused_hours'])} h);"
                 " mixes vCore-hours with instance-hours, so prefer the cost-weighted figure"),
                f"  coverage {pct(cv['ratio'])}  (covered {m(cv['covered'])} of {m(cv['eligible'])} PayG-equivalent)",
                (f"    rows valued at contract UnitPrice: {cv['contract_basis_rows']:,}; excluded for missing price: "
                 f"{cv['excluded_missing_price_rows']:,}"),
                (f"  observed discount gross {m(d['gross'])}, unused commitment cost {m(d['unused_cost'])}, "
                 f"net {m(d['net'])}"),
                "  (definitions: docs/methodology.md; eligibility is inferred from the data)"]
        r = q.reservations(con, f, 10)
        if r["available"] and r["count"]:
            out += ["", f"Reservations with the most unused cost (10 of {r['count']:,}; month grain):"]
            for x in r["rows"]:
                nm = (x["name"] or x["commitment_id"] or "")[-44:]
                out.append(f"  {nm:<46}{pct(x['utilization']):>8}  unused {m(x['unused_cost']):>10}"
                           f"  used {m(x['used_cost']):>11}")
            out.append(f"  reservations with unused hours but no used hours: {r['without_used_rows']} "
                       "(should be 0; non-zero means used/unused rows carry different ids)")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Text overview of imported cost data")
    ap.add_argument("--db", type=Path)
    ap.add_argument("--kind", required=True, choices=sorted(COST_KINDS))
    ap.add_argument("--currency", required=True)
    ap.add_argument("--month", help="YYYY-MM (default: everything)")
    a = ap.parse_args()
    s = Settings.from_env()
    start = end = None
    if a.month:
        start = date.fromisoformat(a.month + "-01")
        end = (date(start.year + (start.month == 12), start.month % 12 + 1, 1) - date.resolution)
    con = duckdb.connect(str(a.db or s.db_path), read_only=True)
    try:
        print(render(con, q.Filters(a.kind, a.currency.upper(), start, end)))
    finally:
        con.close()


if __name__ == "__main__":
    main()
