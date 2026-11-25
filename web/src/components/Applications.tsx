import { useMemo } from "react";
import { filterParams, type Applications as Data, type Filters, type GapRow } from "../api";
import { count, money, num, pct, moneyShort } from "../format";
import { useApi } from "../hooks";
import { Kpi } from "./Kpi";
import { SortTable, type Col } from "./SortTable";
import { Breakdown } from "./Spend";
import { ErrorBox, TimingNote } from "./Status";

interface Props { filters: Filters }

export function Applications({ filters }: Props) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const params = useMemo(() => { const p = filterParams(filters); p.set("n", "15"); return p; }, [filters]);
  const { data, error, loading, reload, timing } = useApi<Data>(ok ? "/api/applications" : null, ok ? params : null);
  const ccy = filters.currency;

  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <p className="empty" aria-live="polite">{ok ? "Loading applications…" : "Fix the date range to load data."}</p>;
  const d = data;
  const gapTotal = num(d.unattributed) + num(d.untagged);

  const cols: Col<GapRow>[] = [
    { key: "service", header: "Service", sortValue: (r) => r.key ?? "", render: (r) => r.key ?? "(none)" },
    { key: "untagged", header: "Untagged", num: true, info: "Resources with no tags at all.", sortValue: (r) => num(r.untagged), render: (r) => money(r.untagged, ccy) },
    { key: "unattributed", header: "Unattributed", num: true, info: "Tagged, but none of the configured application keys.",
      sortValue: (r) => num(r.unattributed), render: (r) => money(r.unattributed, ccy) },
    { key: "cost", header: "Total gap", num: true, sortValue: (r) => num(r.cost), render: (r) => <strong>{money(r.cost, ccy)}</strong> },
  ];

  return (
    <div className={loading ? "loading" : ""} aria-busy={loading}>
      <p className="muted small">
        How much of the cost can be tied to an application, in {ccy}. This reads tags only; it never guesses an owner.
      </p>
      <div className="kpis">
        <Kpi label="Attributed" value={pct(d.attributed_share)} note={money(d.attributed, ccy)} info={d.definition} />
        <Kpi label="Unattributed" value={moneyShort(d.unattributed, ccy)} full={money(d.unattributed, ccy)} note="tagged, no application key" info="Resources with tags, but none of the configured application tag keys." />
        <Kpi label="Untagged" value={moneyShort(d.untagged, ccy)} full={money(d.untagged, ccy)} note="no tags at all" info="Resources with no tags. Some services, such as network circuits, are often untagged by design." />
        <Kpi label="Applications" value={count(d.application_count)} note="distinct values found" />
        <Kpi label="No environment" value={pct(d.environment_none_share)} note={money(d.environment_none, ccy)} info="Cost with no environment tag." />
      </div>

      <div className="grid-3">
        <Breakdown title="Cost by application" dim="application" filters={filters} size={15} />
        <Breakdown title="Cost by environment" dim="environment" filters={filters} size={15} />
      </div>

      <section className="card" aria-label="Where the attribution gap is">
        <header className="card-head"><h3>Where the gap is</h3>
          <span className="muted">{money(gapTotal, ccy)} not tied to an application · {count(d.gap_groups)} services</span></header>
        <SortTable caption="Unattributed and untagged cost by service" columns={cols} rows={d.gap_by_service}
          rowKey={(r) => r.key ?? "(none)"} initialSort={{ key: "cost", desc: true }} empty="Every cost in this selection is attributed."
          footer={d.gap_by_service.length < d.gap_groups ? (
            <tr><th scope="row" colSpan={3}>All other {count(d.gap_groups - d.gap_by_service.length)} services</th>
              <td className="num">{money(d.gap_other, ccy)}</td></tr>
          ) : undefined} />
        <p className="muted small">{d.definition}</p>
      </section>
      <TimingNote timing={timing} loading={loading} />
    </div>
  );
}
