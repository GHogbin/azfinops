import { useMemo } from "react";
import { filterParams, type Filters, type Summary } from "../api";
import { count, money, moneyShort, monthLabel, pct } from "../format";
import { useApi } from "../hooks";

const arrow = (c: number) => (c >= 0 ? "▲" : "▼");
const signed = (c: number) => `${c >= 0 ? "+" : "−"}${pct(Math.abs(c))}`;

/**
 * The headline strip above the Overview: totals over every date with data (the period buttons do not apply), the
 * latest complete month, counts, 30/60/90-day trends and how concentrated cost is in the top services.
 */
export function SummaryStrip({ filters }: { filters: Filters }) {
  const params = useMemo(() => { const p = filterParams({ ...filters, start: null, end: null }); return p; }, [filters]);
  const { data, error } = useApi<Summary>("/api/overview/summary", params);
  const ccy = filters.currency;
  if (error) return <p className="muted small">Summary unavailable: {error}</p>;
  if (!data) return <div className="sum-strip sum-loading" aria-busy="true"><p className="muted small">Loading summary…</p></div>;
  if (!data.available) return null;
  const lm = data.latest_month;
  const top = data.top_services[0];
  const ym = (iso: string | null | undefined) => (iso ? iso.slice(0, 7) : "");
  return (
    <section className="sum" aria-label="Summary of all data">
      <div className="sum-strip">
        <div className="sum-cell accent" title={data.definition}>
          <div className="sum-label">Total cost{filters.combined ? ` · combined in ${ccy}` : ""}</div>
          <div className="sum-value">{money(data.total ?? "0", ccy, 2)}</div>
          <div className="sum-note">{ym(data.first_day)} – {ym(data.last_day)}</div>
        </div>
        <div className="sum-cell">
          <div className="sum-label">Latest complete month</div>
          <div className="sum-value">{lm ? money(lm.cost, ccy, 2) : "n/a"}</div>
          <div className="sum-note">{lm
            ? <>{monthLabel(lm.month)}{lm.change !== null && lm.previous_month && (
                <> · <span className={lm.change >= 0 ? "mv-up" : "mv-down"}>{arrow(lm.change)} {pct(Math.abs(lm.change))}</span> vs {monthLabel(lm.previous_month)}</>)}</>
            : "no month has every day loaded"}</div>
        </div>
        <div className="sum-cell">
          <div className="sum-label">Subscriptions</div>
          <div className="sum-value">{count(data.subscriptions ?? 0)}</div>
          <div className="sum-note">with cost, in the current filters</div>
        </div>
        <div className="sum-cell">
          <div className="sum-label">Resources</div>
          <div className="sum-value">{count(data.resources ?? 0)}</div>
          <div className="sum-note">distinct billed resources</div>
        </div>
      </div>
      <p className="muted small sum-foot">
        {count(data.billing_records ?? 0)} billing records · coverage {data.first_day} to {data.last_day} ({count(data.days_with_data ?? 0)} days) ·
        {" "}the period buttons don't change this strip.
      </p>
      <div className="sum-cards">
        {data.trends.map((t) => {
          const dir = !t.available || t.change === null ? "none" : t.change >= 0 ? "up" : "down";
          return (
            <div key={t.days} className={`sum-card ${dir}`}
              title={`Average cost per day over the last ${t.days} days with data, against the ${t.days} days before.`}>
              <div className="sum-label">{t.days}-day trend</div>
              {t.available && t.change !== null ? (
                <>
                  <div className="sum-value small-value">{signed(t.change)}</div>
                  <div className="sum-note">{t.change >= 0 ? "Up" : "Down"}: {moneyShort(t.per_day ?? "0", ccy)}/day vs {moneyShort(t.previous_per_day ?? "0", ccy)}/day</div>
                </>
              ) : (
                <>
                  <div className="sum-value small-value muted">n/a</div>
                  <div className="sum-note">needs {t.days_needed} days of data; {data.days_with_data} loaded</div>
                </>
              )}
            </div>
          );
        })}
        <div className="sum-card share" title="The three largest services (meter categories) as a share of total cost.">
          <div className="sum-label">Top 3 service share</div>
          <div className="sum-value small-value">{data.top3_share === null ? "n/a" : pct(data.top3_share)}</div>
          <div className="sum-note">{top ? `${top.service ?? "(none)"} leads at ${top.share === null ? "n/a" : pct(top.share)}` : ""}</div>
        </div>
      </div>
    </section>
  );
}
