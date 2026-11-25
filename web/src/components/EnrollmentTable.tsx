import type { Overview as OverviewData } from "../api";
import { currencySymbol, money, moneyShort, monthLabel, num } from "../format";
import { acctName, Dot } from "./EnrollmentSplit";

/** "Sep 26" from "2026-09-01": short enough for twelve month columns side by side. */
function shortMonth(m: string): string {
  const full = monthLabel(m);                       // e.g. "Sep 2026"
  const [mon, yr] = full.split(" ");
  return mon && yr ? `${mon} ’${yr.slice(-2)}` : full;
}

/** Cost per enrollment per month, each in its own currency unless the Combined view converts it. Sized to fit 12 months. */
export function EnrollmentTable({ rows }: { rows: OverviewData["by_account_month"] }) {
  const months = [...new Set(rows.map((r) => r.month))].sort();
  const accounts = [...new Map(rows.map((r) => [`${r.account}|${r.currency}`, r])).values()];
  if (accounts.length === 0) return null;
  const cell = (a: string, c: string, m: string) => rows.find((r) => r.account === a && r.currency === c && r.month === m);
  // Up to 4 months fit in full; beyond that each cell shows the short form, with the exact amount on hover.
  const compact = months.length > 4;
  const show = (v: string | number, ccy: string) => (compact ? moneyShort(v, ccy) : money(v, ccy));
  return (
    <section className="card" aria-label="Cost by enrollment and month">
      <header className="card-head"><h3>Cost by enrollment</h3>
        <span className="muted small">per month, in each enrollment’s currency unless combined{compact ? " · hover a figure for the exact amount" : ""}</span></header>
      <div className="etable-wrap">
        <table className={compact ? "data etable compact" : "data etable"}>
          <thead><tr><th scope="col" className="etable-name">Enrollment</th>
            {months.map((m) => <th scope="col" className="num" key={m} title={monthLabel(m)}>{compact ? shortMonth(m) : monthLabel(m)}</th>)}
            <th scope="col" className="num" title="Latest month against the month before">Change</th></tr></thead>
          <tbody>
            {accounts.map((r) => {
              const vals = months.map((m) => cell(r.account, r.currency, m));
              const last = vals[vals.length - 1], prev = vals[vals.length - 2];
              const delta = last && prev ? num(last.cost) - num(prev.cost) : null;
              const rel = delta !== null && prev && num(prev.cost) !== 0 ? delta / Math.abs(num(prev.cost)) : null;
              return (
                <tr key={`${r.account}|${r.currency}`}>
                  <td className="etable-name" title={`${acctName(r.account)} (${r.account}) · ${r.currency}`}>
                    <span className="etable-acct"><Dot account={r.account} /> <span>{acctName(r.account)}</span></span>
                    <span className="muted small" title={r.currency}>{currencySymbol(r.currency)}</span>
                  </td>
                  {vals.map((v, i) => <td className="num" key={months[i]} title={v ? money(v.cost, r.currency, 2) : "No data"}>{v ? show(v.cost, r.currency) : "—"}</td>)}
                  <td className={`num ${delta === null ? "" : delta >= 0 ? "mv-up" : "mv-down"}`} title={delta === null ? undefined : money(delta, r.currency, 2)}>
                    {delta === null ? "—" : `${delta >= 0 ? "▲" : "▼"} ${show(Math.abs(delta), r.currency)}${rel === null ? "" : ` ${(Math.abs(rel) * 100).toFixed(1)}%`}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
