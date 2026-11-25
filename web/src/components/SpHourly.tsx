import { useMemo } from "react";
import { filterParams, type Filters, type SpHourly as Data } from "../api";
import { dateLabel, money, num } from "../format";
import { useApi } from "../hooks";
import { Kpi } from "./Kpi";

/** Per-hour amount: two decimals, as savings-plan commitments are priced per hour. */
const perHour = (v: string | null | undefined, ccy: string) => (v === null || v === undefined ? "n/a" : `${money(v, ccy, 2)}/h`);

/** Category-level daily averages, not an hourly purchase baseline. */
export function SpHourly({ filters }: { filters: Filters }) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const params = useMemo(() => filterParams(filters), [filters]);
  const { data, error, reload } = useApi<Data>(ok ? "/api/commitments/savings-plan-hourly" : null, ok ? params : null);
  const ccy = filters.currency;
  const amortized = filters.costKind === "amortized";

  return (
    <section className="card" aria-label="Usage per hour not covered by savings plans">
      <header className="card-head"><h3>Usage per hour not covered by savings plans</h3>
        {data?.available && data.last_day && <span className="muted">to {dateLabel(data.last_day)}</span>}</header>
      {error && <p className="error small" role="alert">{error} <button type="button" onClick={reload}>Retry</button></p>}
      {!data && !error && <p className="empty" aria-live="polite">Loading…</p>}
      {data && !data.available && <p className="muted small">{data.reason}</p>}
      {data?.available && (
        <>
          <div className="kpis">
            <Kpi label="Not covered · latest day" value={perHour(data.uncovered_last_day, ccy)}
              note={data.last_day ? dateLabel(data.last_day) : undefined} info={data.definition} />
            <Kpi label="Not covered · 7-day average" value={perHour(data.uncovered_7d, ccy)} note={`${data.days_7d} days`} info={data.definition} />
            <Kpi label="Not covered · 30-day average" value={perHour(data.uncovered_30d, ccy)} note={`${data.days_30d} days with data`} info={data.definition} />
            <Kpi label="Lowest daily average" value={perHour(data.baseline_30d, ccy)}
              note={data.baseline_day ? `lowest day, ${dateLabel(data.baseline_day, false)}` : undefined}
              info={data.definition} />
            <Kpi label="Covered by savings plans" value={perHour(data.covered_7d, ccy)} note="7-day average"
              info="Amortized cost of usage billed through a savings plan, per hour." />
            <Kpi label="Unused savings plan" value={amortized ? perHour(data.unused_7d, ccy) : "n/a"}
              note={amortized ? "7-day average" : "needs the amortized basis"}
              info="UnusedSavingsPlan charges per hour: commitment paid for but not used. A plan with unused commitment should not be topped up." />
          </div>
          {data.by_category_7d.length > 0 && (
            <table className="data">
              <caption className="muted small">Not covered, by meter category · 7-day average per hour</caption>
              <thead><tr><th scope="col">Meter category</th><th scope="col" className="num">Per hour</th><th scope="col" className="num">Share</th></tr></thead>
              <tbody>
                {data.by_category_7d.map((c) => (
                  <tr key={c.category}>
                    <td>{c.category}</td>
                    <td className="num">{perHour(c.per_hour, ccy)}</td>
                    <td className="num">{num(data.uncovered_7d) > 0 ? `${((num(c.per_hour) / num(data.uncovered_7d)) * 100).toFixed(0)}%` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="muted small">
            Per hour = cost per day ÷ 24, because the export is daily: these are daily averages, not hourly peaks. Counted categories:
            {" "}{data.categories.join(", ")}. Some meters in them are not eligible (licences, or App Service plans other than Premium v3 and
            Isolated v2), so treat these as upper figures. Even the lowest daily average is not an hourly floor and cannot size a purchase.
            On-demand only: spot, reservation-covered and Marketplace usage are left out.
          </p>
        </>
      )}
    </section>
  );
}
