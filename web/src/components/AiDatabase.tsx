import { useMemo } from "react";
import { filterParams, type Filters, type Workloads, type WorkloadCategory, type WorkloadGroup } from "../api";
import { count, money, monthLabel, num, pct, moneyShort } from "../format";
import { useApi } from "../hooks";
import { Kpi } from "./Kpi";
import { SortTable, type Col } from "./SortTable";
import { ErrorBox, TimingNote } from "./Status";

interface Props { filters: Filters }

function change(a: number, b: number): string {
  if (a === 0) return b === 0 ? "—" : "new";
  return pct((b - a) / Math.abs(a));
}

function Group({ g, months, total, ccy }: { g: WorkloadGroup; months: string[]; total: number; ccy: string }) {
  const last = num(g.months[g.months.length - 1] ?? 0);
  const prev = num(g.months[g.months.length - 2] ?? 0);
  const cols: Col<WorkloadCategory>[] = [
    { key: "cat", header: "Service", sortValue: (r) => r.category ?? "", render: (r) => r.category ?? "(none)" },
    ...months.map((m, i): Col<WorkloadCategory> => ({
      key: m, header: monthLabel(m), num: true, sortValue: (r) => num(r.months[i] ?? 0), render: (r) => money(r.months[i] ?? 0, ccy),
    })),
    { key: "total", header: "Total", num: true, sortValue: (r) => num(r.total), render: (r) => <strong>{money(r.total, ccy)}</strong> },
  ];
  return (
    <section className="card" aria-label={g.name}>
      <header className="card-head"><h3>{g.name}</h3>
        <span className="muted">{money(g.total, ccy)} · {total === 0 ? "—" : pct(num(g.total) / total)} of cost · {count(g.category_count)} services</span></header>
      {g.category_count === 0 ? (
        <p className="empty">No services in this selection matched {g.name.toLowerCase()}.</p>
      ) : (
        <>
          {months.length >= 2 && (
            <p className="muted small">
              {monthLabel(months[months.length - 2] ?? "")} {money(prev, ccy)} → {monthLabel(months[months.length - 1] ?? "")} {money(last, ccy)}
              {" "}({change(prev, last)}). Months differ in length.
            </p>
          )}
          <SortTable caption={`${g.name} by service and month`} columns={cols} rows={g.categories}
            rowKey={(r) => r.category ?? "(none)"} initialSort={{ key: "total", desc: true }}
            footer={g.categories.length < g.category_count ? (
              <tr><th scope="row" colSpan={1 + months.length}>All other {count(g.category_count - g.categories.length)} services</th>
                <td className="num">{money(g.other, ccy)}</td></tr>) : undefined} />
        </>
      )}
      <details><summary className="small">Name patterns used</summary>
        <p className="muted small">{g.matches.join(", ")}</p></details>
    </section>
  );
}

export function AiDatabase({ filters }: Props) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const params = useMemo(() => filterParams(filters), [filters]);
  const { data, error, loading, reload, timing } = useApi<Workloads>(ok ? "/api/workloads" : null, ok ? params : null);
  const ccy = filters.currency;
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <p className="empty" aria-live="polite">{ok ? "Loading…" : "Fix the date range to load data."}</p>;
  const total = num(data.total);
  const inFamilies = data.groups.reduce((a, g) => a + num(g.total), 0);
  return (
    <div className={loading ? "loading" : ""} aria-busy={loading}>
      <p className="muted small">Spend on AI, data platform and database services in {ccy}, grouped by service name. {data.definition}</p>
      <div className="kpis">
        {data.groups.map((g) => (
          <Kpi key={g.id} label={g.name} value={moneyShort(g.total, ccy)} full={money(g.total, ccy)} note={total === 0 ? undefined : `${pct(num(g.total) / total)} of cost`} />
        ))}
        <Kpi label="All three" value={moneyShort(inFamilies, ccy)} full={money(inFamilies, ccy)} note={total === 0 ? undefined : `${pct(inFamilies / total)} of cost`} />
        <Kpi label="Outside these groups" value={moneyShort(data.outside_families, ccy)} full={money(data.outside_families, ccy)} note="everything else" />
      </div>
      {data.groups.map((g) => <Group key={g.id} g={g} months={data.months} total={total} ccy={ccy} />)}
      <TimingNote timing={timing} loading={loading} />
    </div>
  );
}
