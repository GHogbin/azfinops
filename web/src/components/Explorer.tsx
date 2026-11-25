import { useEffect, useMemo, useState } from "react";
import { DIMS, DIM_LABEL, filterParams, type Dim, type Explore, type Filters, type SeriesPoint } from "../api";
import { count, money, num, pct } from "../format";
import { useApi } from "../hooks";
import { rememberLabel } from "../labels";
import { CostChart } from "./CostChart";
import { Drill } from "./Drill";
import { Dot } from "./EnrollmentSplit";
import { ErrorBox, TimingNote } from "./Status";

interface Props {
  filters: Filters;
  onPick: (dim: Dim, value: string) => void;
  /** Grouping to start with (from the URL, or a drill-through from another workspace). */
  group?: Dim | null;
  /** Reports the grouping so it can be kept in the URL. */
  onGroup?: (dim: Dim) => void;
}

const PAGE_SIZES = [25, 50, 100, 200];

export function Explorer({ filters, onPick, group, onGroup }: Props) {
  const [primary, setPrimary] = useState<Dim>(group ?? "service");
  useEffect(() => { if (group) setPrimary(group); }, [group]);
  useEffect(() => { onGroup?.(primary); }, [primary, onGroup]);
  const [secondary, setSecondary] = useState<Dim | "">("");
  const [sort, setSort] = useState<"cost" | "key">("cost");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const groupBy: Dim[] = secondary && secondary !== primary ? [primary, secondary] : [primary];

  // any change of scope, grouping or sort goes back to the first page
  const scopeKey = JSON.stringify([filters, groupBy, sort, order, pageSize]);
  useEffect(() => setPage(1), [scopeKey]);

  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const params = useMemo(() => {
    const p = filterParams(filters);
    groupBy.forEach((g) => p.append("group_by", g));
    p.set("sort", sort);
    p.set("order", order);
    p.set("page", String(page));
    p.set("page_size", String(pageSize));
    return p;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey, page]);
  const { data, error, loading, timing, reload } = useApi<Explore>(ok ? "/api/cost/explore" : null, ok ? params : null);

  const [open, setOpen] = useState<{ dim: Dim; key: string; name: string } | null>(null);
  useEffect(() => setOpen(null), [primary, secondary]);
  const [grain, setGrain] = useState<"day" | "month">("day");
  const seriesParams = useMemo(() => { const p = filterParams(filters); p.set("grain", grain); return p; }, [filters, grain]);
  const series = useApi<SeriesPoint[]>(ok ? "/api/cost/series" : null, ok ? seriesParams : null);

  const setSortBy = (s: "cost" | "key") => {
    if (s === sort) setOrder((o) => (o === "desc" ? "asc" : "desc"));
    else { setSort(s); setOrder(s === "cost" ? "desc" : "asc"); }
  };
  const ariaSort = (s: "cost" | "key") => (sort === s ? (order === "desc" ? "descending" : "ascending") : "none");
  const ccy = filters.currency;

  return (
    <div>
      <div className="filter-row">
        <label className="field">
          <span>Group by</span>
          <select value={primary} onChange={(e) => setPrimary(e.target.value as Dim)}>
            {DIMS.map((d) => <option key={d} value={d}>{DIM_LABEL[d]}</option>)}
          </select>
        </label>
        <label className="field">
          <span>then by</span>
          <select value={secondary} onChange={(e) => setSecondary(e.target.value as Dim | "")}>
            <option value="">(none)</option>
            {DIMS.filter((d) => d !== primary).map((d) => <option key={d} value={d}>{DIM_LABEL[d]}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Rows per page</span>
          <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>

      {series.data && (
        <div className={series.loading ? "loading" : ""}>
          <div className="chart-controls">
            <div className="seg" role="group" aria-label="Chart grain">
              <button type="button" aria-pressed={grain === "day"} onClick={() => setGrain("day")}>Daily</button>
              <button type="button" aria-pressed={grain === "month"} onClick={() => setGrain("month")}>Monthly</button>
            </div>
          </div>
          <CostChart points={series.data} currency={ccy} grain={grain} measure="total"
            title={`${grain === "day" ? "Daily" : "Monthly"} cost (${ccy}) for the current filters`} />
        </div>
      )}
      {error && <ErrorBox message={error} onRetry={reload} />}
      {!data && !error && <p className="empty" aria-live="polite">{ok ? "Loading…" : "Fix the date range to load data."}</p>}
      {data && (
        <section className={`card${loading ? " loading" : ""}`} aria-busy={loading} aria-label="Cost by group">
          <div className="table-scroll">
            <table className="data">
              <caption className="sr-only">Cost grouped by {groupBy.map((g) => DIM_LABEL[g]).join(" then ")}, {sort === "cost" ? "sorted by cost" : "sorted by name"}</caption>
              <thead>
                <tr>
                  {groupBy.map((g, i) => (
                    <th key={g} scope="col" aria-sort={i === 0 ? ariaSort("key") : undefined}>
                      {i === 0 ? <button type="button" className="th-btn" onClick={() => setSortBy("key")}>{DIM_LABEL[g]}{sort === "key" && (order === "asc" ? " ▲" : " ▼")}</button> : DIM_LABEL[g]}
                    </th>
                  ))}
                  <th scope="col" className="num" aria-sort={ariaSort("cost")}>
                    <button type="button" className="th-btn" onClick={() => setSortBy("cost")}>Cost ({ccy}){sort === "cost" && (order === "asc" ? " ▲" : " ▼")}</button>
                  </th>
                  <th scope="col" className="num">Share</th>
                  <th scope="col" className="num">Source rows</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.length === 0 && <tr><td colSpan={groupBy.length + 3} className="empty">No rows on this page.</td></tr>}
                {data.rows.map((r) => {
                  r.keys.forEach((k, i) => rememberLabel(k, r.labels[i] ?? null));
                  const tot = num(data.total);
                  return (
                    <tr key={r.keys.map((k) => k ?? "\u0000").join("|")}>
                      {r.keys.map((k, i) => {
                        const dim = groupBy[i] as Dim;
                        const name = r.labels[i] ?? k ?? "(none)";
                        return (
                          <td key={dim}>
                            {dim === "billing_account" && k !== null && <Dot account={k} />}
                            {k !== null && i === 0
                              ? <button type="button" className="link-btn" aria-expanded={open?.key === k && open.dim === dim}
                                  title={`Show what is inside ${name}`}
                                  onClick={() => setOpen(open?.key === k && open.dim === dim ? null : { dim, key: k, name })}>
                                  {open?.key === k && open.dim === dim ? "▾ " : "▸ "}{name}</button>
                              : k !== null
                                ? <button type="button" className="link-btn" title={`Filter to ${name}`} onClick={() => onPick(dim, k)}>{name}</button>
                                : <span className="muted">{name}</span>}
                            {r.labels[i] && k && <div className="muted small mono">{k}</div>}
                          </td>
                        );
                      })}
                      <td className="num">{money(r.cost, ccy, 2)}</td>
                      <td className="num">{tot === 0 ? "n/a" : pct(num(r.cost) / tot)}</td>
                      <td className="num">{count(r.rows)}</td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" colSpan={groupBy.length}>Total, all {count(data.groups)} groups</th>
                  <td className="num">{money(data.total, ccy, 2)}</td><td className="num">100%</td><td />
                </tr>
              </tfoot>
            </table>
          </div>
          <nav className="pager" aria-label="Pages">
            <button type="button" disabled={page <= 1} onClick={() => setPage(1)}>« First</button>
            <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>‹ Previous</button>
            <span>Page {data.page} of {count(data.pages)}</span>
            <button type="button" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>Next ›</button>
            <button type="button" disabled={page >= data.pages} onClick={() => setPage(data.pages)}>Last »</button>
          </nav>
        </section>
      )}
      {open && (
        <Drill filters={filters} dim={open.dim} value={open.key} label={open.name}
          onFilter={() => { onPick(open.dim, open.key); setOpen(null); }} onClose={() => setOpen(null)} />
      )}
      <TimingNote timing={timing} loading={loading} />
    </div>
  );
}
