import { useEffect, useMemo, useState } from "react";
import {
  filterParams, type AccountMonth, type Explore, type Filters, type Meta, type Movement, type MovementRow, type TopRow,
} from "../api";
import { count, money, monthLabel, monthsBetween, num, moneyShort } from "../format";
import { useApi } from "../hooks";
import { BarList } from "./BarList";
import { EnrollmentTable } from "./EnrollmentTable";
import { Kpi } from "./Kpi";
import { SortTable, type Col } from "./SortTable";
import { ErrorBox, TimingNote } from "./Status";

interface Props { filters: Filters; meta: Meta }

const GLOSSARY: [string, string][] = [
  ["Usage", "Consumption of a metered service. On the amortized basis this also carries the spread cost of reservations and savings plans."],
  ["Purchase", "A one-off or recurring purchase, such as a commitment or a marketplace subscription. In amortized exports the cost is spread into usage, so this is often zero."],
  ["Refund", "A credit returned against an earlier charge. Shown as a negative number and included in totals."],
  ["UnusedReservation / UnusedSavingsPlan", "The part of a commitment that nothing used. Only visible on the amortized basis."],
];

/** Cost per enrollment per month, moved here from the Overview. */
function EnrollmentMonthly({ filters }: { filters: Filters }) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const params = useMemo(() => filterParams(filters), [filters]);
  const { data, error, reload } = useApi<AccountMonth[]>(ok ? "/api/accounts/monthly" : null, ok ? params : null);
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return null;
  return new Set(data.map((r) => r.account)).size > 1 ? <EnrollmentTable rows={data} /> : null;
}

/** One breakdown through the paged endpoint (all groups fit one page for these low-cardinality dimensions). */
export function Breakdown({ title, dim, filters, usageOnly, size = 50 }: { title: string; dim: "charge_type" | "pricing_model" | "publisher_type" | "application" | "environment"; filters: Filters; usageOnly?: boolean; size?: number }) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const params = useMemo(() => {
    const f: Filters = usageOnly ? { ...filters, dims: { ...filters.dims, charge_type: ["Usage"] } } : filters;
    const p = filterParams(f);
    p.append("group_by", dim);
    p.set("page_size", String(size));
    return p;
  }, [filters, dim, usageOnly, size]);
  const { data, error, loading, reload } = useApi<Explore>(ok ? "/api/cost/explore" : null, ok ? params : null);
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <section className="card"><h3>{title}</h3><p className="empty">Loading…</p></section>;
  const rows: TopRow[] = data.rows.map((r) => ({ key: r.keys[0] ?? null, label: r.labels[0] ?? null, cost: r.cost }));
  const shown = rows.reduce((a, r) => a + num(r.cost), 0);
  return (
    <div className={loading ? "loading" : ""}>
      <BarList title={title} rows={rows} other={String(num(data.total) - shown)} total={data.total} groups={data.groups} currency={filters.currency} />
    </div>
  );
}

const KIND_LABEL: Record<MovementRow["kind"], string> = {
  price_volume: "Price + volume", new: "New meter", removed: "Removed meter", no_unit_price: "No unit price",
};

export function Spend({ filters, meta }: Props) {
  const ccy = filters.currency;
  const scope = meta.scopes.find((s) => s.cost_kind === filters.costKind && s.currency === filters.currency);
  const months = useMemo(() => (scope ? monthsBetween(scope.first_date, scope.last_date) : []), [scope]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  // default: the two most recent months; re-pick if the scope changes underneath us
  useEffect(() => {
    if (months.length >= 2 && (!months.includes(from) || !months.includes(to) || from === to)) {
      setFrom(months[months.length - 2] ?? "");
      setTo(months[months.length - 1] ?? "");
    }
  }, [months, from, to]);

  const ready = months.length >= 2 && !!from && !!to && from !== to;
  const params = useMemo(() => {
    const f: Filters = { ...filters, start: null, end: null };   // the two months are chosen here, not by the date range
    const p = filterParams(f);
    p.set("month_from", from);
    p.set("month_to", to);
    p.set("n", "15");
    return p;
  }, [filters, from, to]);
  const mv = useApi<Movement>(ready ? "/api/cost/movement" : null, ready ? params : null);

  const cols: Col<MovementRow>[] = [
    { key: "name", header: "Meter", sortValue: (r) => r.name ?? r.meter_id,
      render: (r) => <><span>{r.name ?? r.meter_id}</span><div className="muted small">{r.category ?? "—"} · {r.unit || "no unit"}</div></> },
    { key: "kind", header: "Kind", sortValue: (r) => KIND_LABEL[r.kind], render: (r) => KIND_LABEL[r.kind] },
    { key: "from", header: monthLabel(from) || "From", num: true, sortValue: (r) => num(r.cost_from), render: (r) => money(r.cost_from, ccy) },
    { key: "to", header: monthLabel(to) || "To", num: true, sortValue: (r) => num(r.cost_to), render: (r) => money(r.cost_to, ccy) },
    { key: "change", header: "Change", num: true, sortValue: (r) => Math.abs(num(r.change)), render: (r) => <strong>{money(r.change, ccy)}</strong> },
    { key: "vol", header: "Volume effect", num: true, info: "(quantity change) × previous unit price",
      sortValue: (r) => (r.volume === null ? null : num(r.volume)), render: (r) => (r.volume === null ? "—" : money(r.volume, ccy)) },
    { key: "price", header: "Price effect", num: true, info: "cost change minus volume effect",
      sortValue: (r) => (r.price === null ? null : num(r.price)), render: (r) => (r.price === null ? "—" : money(r.price, ccy)) },
  ];
  const d = mv.data;

  return (
    <div>
      <p className="muted small">
        Signed values in {ccy}, nothing converted. Refunds and credits appear as negatives and are included in every total.
      </p>
      <EnrollmentMonthly filters={filters} />
      <div className="grid-3">
        <Breakdown title="By charge type" dim="charge_type" filters={filters} />
        <Breakdown title="Usage by pricing model" dim="pricing_model" filters={filters} usageOnly />
        <Breakdown title="Usage by publisher" dim="publisher_type" filters={filters} usageOnly />
      </div>
      <details className="card">
        <summary>What the charge types mean</summary>
        <ul className="defs">{GLOSSARY.map(([k, v]) => <li key={k}><strong>{k}.</strong> {v}</li>)}</ul>
      </details>

      <section className="card" aria-label="Why did cost change between two months">
        <header className="card-head"><h3>Why did usage cost change?</h3></header>
        {months.length < 2 ? (
          <p className="empty">Needs at least two months of data in this scope. Import another month to compare.</p>
        ) : (
          <>
            <div className="filter-row">
              <label className="field"><span>Compare</span>
                <select value={from} onChange={(e) => setFrom(e.target.value)}>
                  {months.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
                </select></label>
              <label className="field"><span>with</span>
                <select value={to} onChange={(e) => setTo(e.target.value)}>
                  {months.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
                </select></label>
              {from === to && <p className="error small" role="alert">Choose two different months.</p>}
            </div>
            {mv.error && <ErrorBox message={mv.error} onRetry={mv.reload} />}
            {!d && !mv.error && ready && <p className="empty" aria-live="polite">Loading…</p>}
            {d && !d.available && <p className="banner warn" role="status">{d.reason}</p>}
            {d && d.available && (
              <div className={mv.loading ? "loading" : ""} aria-busy={mv.loading}>
                <div className="kpis">
                  <Kpi label="Total change" value={moneyShort(d.change ?? 0, ccy)} full={money(d.change ?? 0, ccy)} note={`${monthLabel(d.month_from ?? "")} → ${monthLabel(d.month_to ?? "")}`} info="Usage cost in the later month minus the earlier month." />
                  <Kpi label="Volume" value={moneyShort(d.volume ?? 0, ccy)} full={money(d.volume ?? 0, ccy)} note="more or less consumed" info="Quantity change valued at the earlier month's unit price." />
                  <Kpi label="Price" value={moneyShort(d.price ?? 0, ccy)} full={money(d.price ?? 0, ccy)} note="unit price changed" info="Cost change not explained by volume." />
                  <Kpi label="New meters" value={moneyShort(d.new ?? 0, ccy)} full={money(d.new ?? 0, ccy)} note="not used in the earlier month" />
                  <Kpi label="Removed meters" value={moneyShort(d.removed ?? 0, ccy)} full={money(d.removed ?? 0, ccy)} note="not used in the later month" />
                  <Kpi label="Not split" value={moneyShort(d.no_unit_price ?? 0, ccy)} full={money(d.no_unit_price ?? 0, ccy)} note="zero quantity in a month" info="Meters with zero quantity in either month have no unit price, so the change cannot be split into price and volume." />
                </div>
                {d.reconciles === false && <p className="banner error" role="alert">The parts do not add up to the total change. Please report this.</p>}
                <SortTable caption="Largest movers between the two months" columns={cols} rows={d.rows}
                  rowKey={(r) => `${r.meter_id}|${r.unit}`} initialSort={{ key: "change", desc: true }}
                  footer={d.rows.length < d.groups ? (
                    <tr><th scope="row" colSpan={4}>All other {count(d.groups - d.rows.length)} meters</th>
                      <td className="num">{money(d.other ?? 0, ccy)}</td><td /><td /></tr>
                  ) : undefined} />
                <p className="muted small">
                  {d.definition} Monthly grain: the date range above is ignored here.
                  {" "}Months have different numbers of days, so fixed monthly charges can show a small volume change
                  (30 days against 31 is about 3%) that is not a real change. On the amortized basis, unit price is cost divided
                  by quantity, so reservation and savings-plan discounts also appear as price effects.
                  {d.filters_not_applied.length > 0 && ` Not applied at this grain: ${d.filters_not_applied.join(", ")}.`}
                </p>
              </div>
            )}
          </>
        )}
      </section>
      <TimingNote timing={mv.timing} loading={mv.loading} />
    </div>
  );
}
