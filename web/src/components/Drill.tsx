import { useMemo } from "react";
import { DIM_LABEL, filterParams, type BreakdownRow, type Dim, type Explore, type Filters, type ServiceBreakdown } from "../api";
import { count, money, num, pct } from "../format";
import { useApi } from "../hooks";
import { SortTable, type Col } from "./SortTable";
import { ErrorBox, TimingNote } from "./Status";

interface Props {
  filters: Filters;
  dim: Dim;
  value: string;
  label: string;
  onFilter: () => void;
  onClose: () => void;
}

/** What is inside one row of the Explorer table. A service opens into its meters; any other group opens into its services. */
export function Drill({ filters, dim, value, label, onFilter, onClose }: Props) {
  return (
    <section className="card drill" aria-label={`Breakdown of ${label}`}>
      <header className="card-head">
        <h3>{label}</h3>
        <span className="drill-actions">
          <button type="button" className="chip-btn" onClick={onFilter}>Filter to this {DIM_LABEL[dim].toLowerCase()}</button>
          <button type="button" className="chip-btn" onClick={onClose} aria-label="Close breakdown">Close</button>
        </span>
      </header>
      {dim === "service" ? <Meters filters={filters} service={value} /> : <Services filters={filters} dim={dim} value={value} />}
    </section>
  );
}

function Meters({ filters, service }: { filters: Filters; service: string }) {
  const params = useMemo(() => { const p = filterParams(filters); p.set("service", service); p.set("n", "25"); return p; }, [filters, service]);
  const { data, error, loading, reload, timing } = useApi<ServiceBreakdown>("/api/cost/breakdown", params);
  const ccy = filters.currency;
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <p className="empty" aria-live="polite">Loading meters…</p>;
  if (!data.available) return <p className="banner warn" role="status">{data.reason}</p>;
  const cols: Col<BreakdownRow>[] = [
    { key: "name", header: "Meter", sortValue: (r) => r.name ?? r.meter_id,
      render: (r) => (
        <>
          <span>{r.name ?? r.meter_id}</span>
          <div className="muted small">{r.unit || "no unit"}</div>
          {r.variants > 1 && (
            <details className="meter-parts">
              <summary className="small">{r.variants} meter IDs: regions and variants</summary>
              <ul className="small">
                {r.parts.map((x) => (
                  <li key={x.meter_id} title={x.meter_id}>
                    <span>{[x.subcategory, x.region].filter(Boolean).join(" · ") || x.meter_id.slice(0, 8)}</span>
                    <span className="num">{money(x.cost, ccy)}</span>
                  </li>
                ))}
                {r.variants > r.parts.length && <li className="muted">and {r.variants - r.parts.length} more</li>}
              </ul>
            </details>
          )}
        </>
      ) },
    { key: "qty", header: "Usage quantity", num: true, info: "Usage rows only. Comparable within one meter, not across meters.",
      sortValue: (r) => (r.quantity === null ? null : num(r.quantity)), render: (r) => (r.quantity === null ? "—" : count(Math.round(num(r.quantity)))) },
    { key: "od", header: "On demand", num: true, sortValue: (r) => num(r.on_demand_cost), render: (r) => money(r.on_demand_cost, ccy) },
    { key: "cost", header: "Cost", num: true, sortValue: (r) => num(r.cost), render: (r) => <strong>{money(r.cost, ccy)}</strong> },
    { key: "share", header: "Share", num: true, sortValue: (r) => r.share,
      render: (r) => (r.share === null ? "n/a" : (
        <span className="util"><span className="bar-track"><span className="bar-fill" style={{ width: `${Math.min(100, Math.max(0, Math.abs(r.share) * 100))}%` }} /></span>{pct(r.share)}</span>)) },
  ];
  return (
    <div className={loading ? "loading" : ""} aria-busy={loading}>
      <SortTable caption={`Meters in ${data.service}`} columns={cols} rows={data.rows} rowKey={(r) => `${r.name ?? r.meter_id}|${r.unit}`}
        initialSort={{ key: "cost", desc: true }}
        footer={data.rows.length < data.groups ? (
          <tr><th scope="row" colSpan={3}>All other {count(data.groups - data.rows.length)} meters</th>
            <td className="num">{money(data.other ?? 0, ccy)}</td><td /></tr>
        ) : undefined} />
      <p className="muted small">
        {data.definition}
        {data.filters_not_applied.length > 0 && ` Not applied at this grain: ${data.filters_not_applied.join(", ")}.`}
      </p>
      <TimingNote timing={timing} loading={loading} />
    </div>
  );
}

function Services({ filters, dim, value }: { filters: Filters; dim: Dim; value: string }) {
  const params = useMemo(() => {
    const p = filterParams({ ...filters, dims: { ...filters.dims, [dim]: [value] } });
    p.append("group_by", "service");
    p.set("page_size", "25");
    return p;
  }, [filters, dim, value]);
  const { data, error, loading, reload } = useApi<Explore>("/api/cost/explore", params);
  const ccy = filters.currency;
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <p className="empty" aria-live="polite">Loading services…</p>;
  const total = num(data.total);
  return (
    <div className={loading ? "loading" : ""} aria-busy={loading}>
      <div className="table-scroll">
        <table className="data">
          <caption className="sr-only">Services inside this {DIM_LABEL[dim].toLowerCase()}</caption>
          <thead><tr><th scope="col">Service</th><th scope="col" className="num">Cost ({ccy})</th><th scope="col" className="num">Share</th></tr></thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.keys[0] ?? "(none)"}><td>{r.labels[0] ?? r.keys[0] ?? "(none)"}</td>
                <td className="num">{money(r.cost, ccy, 2)}</td><td className="num">{total === 0 ? "n/a" : pct(num(r.cost) / total)}</td></tr>
            ))}
          </tbody>
          <tfoot><tr><th scope="row">Total, {count(data.groups)} services</th><td className="num">{money(data.total, ccy, 2)}</td><td className="num">100%</td></tr></tfoot>
        </table>
      </div>
      <p className="muted small">Same filters and date range as the table above, with this {DIM_LABEL[dim].toLowerCase()} added.</p>
    </div>
  );
}
