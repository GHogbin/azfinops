import { useMemo, useState } from "react";
import { filterParams, type ChangeKind, type Filters, type ResourceChange, type ResourceChanges as Data } from "../api";
import { toCsv } from "../csv";
import { count, dateLabel, money, moneyShort, monthLabel, num } from "../format";
import { useApi } from "../hooks";
import { SplitBar } from "./EnrollmentSplit";
import { Kpi } from "./Kpi";

const TABS: { id: ChangeKind; label: string }[] = [
  { id: "decommissioned", label: "Decommissioned" },
  { id: "downsized", label: "Downsized" },
  { id: "upsized", label: "Upsized" },
];

/** Cost change as the What changed card shows it: ▼ for a lower monthly run-rate, ▲ for a higher one. */
function Change({ saving, ccy }: { saving: string; ccy: string }) {
  const v = num(saving);
  if (v === 0) return <span className="muted">0</span>;
  return <span className={v > 0 ? "mv-down" : "mv-up"} title={`${money(-v, ccy, 2)} per month at run-rate`}>
    {v > 0 ? "▼" : "▲"} {moneyShort(Math.abs(v), ccy)}</span>;
}

const INTRO: Record<ChangeKind, string> = {
  decommissioned: "Running at the end of the earlier month and stopped before the end of this one.",
  downsized: "Moved to a size or tier that costs less per day.",
  upsized: "Moved to a size or tier that costs more per day.",
};

const sku = (r: ResourceChange) => r.kind === "decommissioned"
  ? `${r.sku_from ?? "?"} → ${r.stopped_on ? `stopped ${dateLabel(r.stopped_on, false)}` : "no usage"}`
  : `${r.sku_from ?? "?"} → ${r.sku_to ?? "?"}`;

/** One change type: its own service filter, CSV download and table. */
function ChangeSection({ kind, label, data, ccy }: { kind: ChangeKind; label: string; data: Data; ccy: string }) {
  const [service, setService] = useState<string>("");
  const ofKind = data.rows.filter((r) => r.kind === kind);
  const total = data.totals[kind];
  const services = [...new Set(ofKind.map((r) => r.service ?? "(none)"))].sort();
  const svc = services.includes(service) ? service : "";
  const rows = svc ? ofKind.filter((r) => (r.service ?? "(none)") === svc) : ofKind;
  const from = data.month_from ? monthLabel(data.month_from) : "";
  const to = data.month_to ? monthLabel(data.month_to) : "";

  function download() {
    const csv = toCsv(
      ["change", "resource_name", "resource_id", "subscription", "resource_group", "service", "sku_from", "sku_to", "stopped_on",
        `cost_${data.month_from}`, `cost_${data.month_to}`, "rate_per_day_before", "rate_per_day_after", `saving_per_month_${ccy}`],
      rows.map((r) => [r.kind, r.resource_name, r.resource_id, r.subscription ?? r.subscription_id, r.resource_group, r.service,
        r.sku_from, r.sku_to, r.stopped_on, r.cost_from, r.cost_to, r.rate_from, r.rate_to, r.saving]));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `resource-changes-${kind}-${data.month_to}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <section className="card rc-section" aria-label={label}>
      <header className="card-head">
        <h3>{label} · {count(total.count)} resources · <Change saving={total.saving} ccy={ccy} /> per month</h3>
        <div className="rc-bar">
          {ofKind.length > 0 && (
            <label className="field rc-svc">Service
              <select value={svc} onChange={(e) => setService(e.target.value)}>
                <option value="">All ({count(ofKind.length)})</option>
                {services.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </label>
          )}
          <button type="button" onClick={download} disabled={rows.length === 0}>⤓ CSV</button>
        </div>
      </header>
      <p className="muted small">{INTRO[kind]}</p>
      {rows.length === 0 ? <p className="muted small">None in this selection.</p> : (
        <div className="table-scroll rc-table">
          <table className="data">
            <thead><tr>
              <th scope="col">Resource</th><th scope="col">Subscription / resource group</th><th scope="col">Size or tier</th>
              <th scope="col" className="num">{from}</th><th scope="col" className="num">{to}</th>
              <th scope="col" className="num">Change per month</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.resource_id}>
                  <td title={r.resource_id}>{r.resource_name ?? r.resource_id.split("/").pop()}</td>
                  <td>{r.subscription ?? r.subscription_id ?? "—"}<span className="muted"> / {r.resource_group ?? "—"}</span></td>
                  <td>{sku(r)}</td>
                  <td className="num">{money(r.cost_from, ccy)}</td>
                  <td className="num">{money(r.cost_to, ccy)}</td>
                  <td className="num"><Change saving={r.saving} ccy={ccy} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {ofKind.length < total.count && <p className="muted small">The {count(ofKind.length)} largest of {count(total.count)} are listed.</p>}
    </section>
  );
}

/** Resources that were decommissioned or resized, compared with the month before. */
export function ResourceChanges({ filters }: { filters: Filters }) {
  const params = useMemo(() => { const p = filterParams(filters); p.set("n", "500"); return p; }, [filters]);
  const { data, error, reload } = useApi<Data>("/api/optimization/resource-changes", params);
  const ccy = filters.currency;

  if (error) return <section className="card"><h3>Rightsized and decommissioned</h3><p className="muted small">Could not load: {error} <button type="button" onClick={reload}>Retry</button></p></section>;
  if (!data) return <section className="card" aria-busy="true"><h3>Rightsized and decommissioned</h3><p className="muted small">Loading…</p></section>;

  const from = data.month_from ? monthLabel(data.month_from) : "";
  const to = data.month_to ? monthLabel(data.month_to) : "";
  const title = `Rightsized and decommissioned${data.month_from && data.month_to ? ` · ${to} vs ${from}` : ""}`;
  if (!data.available) return <section className="card"><h3>{title}</h3><p className="muted small">{data.reason}</p></section>;

  const t = data.totals;

  return (
    <section className="rc" aria-label="Rightsized and decommissioned resources">
      <h2 className="rc-title">{title}</h2>
      <div className="kpis rc-kpis">
        {TABS.map(({ id, label }) => (
          <Kpi key={id} label={label} value={`${count(t[id].count)} resources`} full={money(t[id].saving, ccy, 2)}
            note={`${num(t[id].saving) >= 0 ? "▼" : "▲"} ${moneyShort(Math.abs(num(t[id].saving)), ccy)} per month`} />
        ))}
        <Kpi label="Net change" value={`${num(data.net_saving) >= 0 ? "▼" : "▲"} ${moneyShort(Math.abs(num(data.net_saving)), ccy)}/mo`}
          full={money(-num(data.net_saving), ccy, 2)} note="monthly run-rate" info={data.definition}>
          <SplitBar label="Net change by enrollment"
            items={data.by_account.filter((a) => a.account !== null).map((a) => ({ account: a.account as string, value: num(a.saving), text: moneyShort(a.saving, ccy), currency: ccy }))} />
        </Kpi>
      </div>

      <p className="muted small">
        Change per month is a run-rate: cost per day before and after, times {data.days_to} days, so a change made mid-month counts in full.
        {(data.complete_from === false || data.complete_to === false) && " One of the two months is incomplete."}
        {data.filters_not_applied.length > 0 && ` Not applied here: ${data.filters_not_applied.join(", ")}.`}
      </p>
      {TABS.map(({ id, label }) => <ChangeSection key={id} kind={id} label={label} data={data} ccy={ccy} />)}
    </section>
  );
}
