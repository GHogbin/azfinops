import { useMemo, useState } from "react";
import { DIM_LABEL, DIMS, filterParams, postJson, type Analysis, type Budget, type Dim, type Filters, type SavedView } from "../api";
import { accumulate, alignPrevious, budgetLine, forecast as project, withLocalFilters } from "../analysis";
import { toCsv } from "../csv";
import { dateLabel, money, moneyShort, monthLabel, num, pct, periodLabel } from "../format";
import { useApi } from "../hooks";
import { labelFor } from "../labels";
import type { AnalysisOpts, ChartKind, Granularity } from "../state";
import { AnalysisChart, type ChartSeries } from "./AnalysisChart";
import { payColor } from "./BentoTiles";
import { acctName, enrollmentColor } from "./EnrollmentSplit";
import { Kpi } from "./Kpi";
import { MultiSelect } from "./MultiSelect";
import { ErrorBox, TimingNote } from "./Status";

const OTHER = "__other__";
const GRANS: { id: Granularity; label: string }[] = [{ id: "acc", label: "Accumulated" }, { id: "day", label: "Daily" }, { id: "month", label: "Monthly" }];
const CHARTS: { id: ChartKind; label: string }[] = [
  { id: "area", label: "Stacked area" }, { id: "column", label: "Stacked column" }, { id: "grouped", label: "Grouped column" },
  { id: "line", label: "Line" }, { id: "donut", label: "Donut" }, { id: "table", label: "Table" },
];
const PALETTE = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => `var(--cat-${i})`);

interface Props {
  filters: Filters;
  opts: AnalysisOpts;
  onOpts: (o: AnalysisOpts) => void;
  onPick: (dim: Dim, value: string) => void;
}

/** One chart reshaped with group by, granularity and chart type, like Cost analysis in the Azure portal. */
export function CostAnalysis({ filters: dashboard, opts, onOpts, onPick }: Props) {
  // The dashboard filters plus this chart's own; everything below reads the combined set.
  const filters = useMemo(() => withLocalFilters(dashboard, opts.dims), [dashboard, opts.dims]);
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const grain = opts.gran === "month" ? "month" : "day";
  const params = useMemo(() => {
    const p = filterParams(filters);
    p.set("dimension", opts.dim); p.set("grain", grain); p.set("n", "8");
    if (opts.compare) p.set("compare", "true");
    return p;
  }, [filters, opts.dim, grain, opts.compare]);
  const { data, error, loading, timing, reload } = useApi<Analysis>(ok ? "/api/cost/analysis" : null, ok ? params : null);
  const budgets = useApi<{ budgets: Budget[] }>("/api/budgets", null);
  const views = useApi<{ views: SavedView[] }>("/api/views", null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [viewName, setViewName] = useState("");
  const [budgetDraft, setBudgetDraft] = useState<string | null>(null);
  const [fail, setFail] = useState<string | null>(null);
  const [adding, setAdding] = useState<Dim[]>([]);
  const localDims = DIMS.filter((d) => (opts.dims[d]?.length ?? 0) > 0 || adding.includes(d));
  const localChips = DIMS.flatMap((d) => (opts.dims[d] ?? []).map((v) => ({ d, v })));
  const setLocal = (d: Dim, vals: string[]) => {
    const dims = { ...opts.dims };
    if (vals.length) dims[d] = vals; else delete dims[d];
    setHidden(new Set());
    onOpts({ ...opts, dims });
  };
  const ccy = filters.currency;
  const set = (o: Partial<AnalysisOpts>) => onOpts({ ...opts, ...o });

  const scopeIds = filters.dims.billing_account ?? [];
  const scope = scopeIds.length === 1 ? (scopeIds[0] as string) : "all";
  const scopeName = scope === "all" ? (filters.combined ? `all enrollments, combined in ${ccy}` : `everything in ${ccy}`) : acctName(scope);
  const budget = budgets.data?.budgets.find((b) => b.scope === scope && b.currency === ccy) ?? null;

  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!ok) return <p className="empty">Fix the date range to load data.</p>;

  const acc = opts.gran === "acc";
  const name = (k: string | null, label: string | null) => {
    if (k === null) return "(none)";
    if (k === OTHER) return `Others (${data?.grouped ?? 0})`;
    if (opts.dim === "billing_account") return label ?? acctName(k);
    return label ?? k;
  };
  const color = (k: string | null, i: number) => {
    if (k === OTHER) return "var(--muted)";
    if (opts.dim === "billing_account" && k) return enrollmentColor(k);
    if (opts.dim === "pricing_model" && k) return payColor(k, i);
    return PALETTE[i % PALETTE.length] as string;
  };
  const all: ChartSeries[] = (data?.series ?? []).map((s, i) => {
    const v = s.values.map(num);
    return { key: s.key ?? "(none)", name: name(s.key, s.label), color: color(s.key, i), values: acc ? accumulate(v) : v };
  });
  const shown = all.filter((s) => !hidden.has(s.key));
  const x = data?.periods ?? [];
  const dailyShown = x.map((_d, i) => (data?.series ?? []).filter((s) => !hidden.has(s.key ?? "(none)")).reduce((a, s) => a + num(s.values[i]), 0));
  const fc = acc && opts.forecast && data?.available ? project(x, dailyShown, filters.end) : null;
  const prev = opts.compare && data?.previous ? alignPrevious(data.previous.totals.map(num), x.length, acc) : null;
  const bAmt = budget ? num(budget.amount) : null;
  const bLine = opts.budget && bAmt && opts.chart !== "donut" ? budgetLine([...x, ...(fc?.days ?? [])], opts.gran, bAmt) : null;
  const xLabel = (iso: string, short = false) => (grain === "month" ? monthLabel(iso) : dateLabel(iso, !short));

  const total = dailyShown.reduce((a, v) => a + v, 0);
  const prevTotal = data?.previous ? num(data.previous.total) : null;
  const change = prevTotal === null ? null : total - prevTotal;
  const monthEnd = fc ? fc.values[fc.values.length - 1] ?? null : null;
  const oneMonth = x.length > 0 && (x[0] as string).slice(0, 7) === (x[x.length - 1] as string).slice(0, 7);

  async function saveView() {
    setFail(null);
    try {
      await postJson("/api/views", { name: viewName.trim(), query: window.location.search }, new AbortController().signal);
      setViewName(""); views.reload();
    } catch (e) { setFail(e instanceof Error ? e.message : "Could not save the view"); }
  }
  async function deleteView(n: string) {
    try { await postJson("/api/views", { name: n, query: null }, new AbortController().signal); views.reload(); }
    catch (e) { setFail(e instanceof Error ? e.message : "Could not delete the view"); }
  }
  async function saveBudget(amount: string | null) {
    setFail(null);
    try {
      await postJson("/api/budgets", { scope, currency: ccy, amount }, new AbortController().signal);
      setBudgetDraft(null); budgets.reload();
    } catch (e) { setFail(e instanceof Error ? e.message : "Could not save the budget"); }
  }
  function download() {
    const cols = ["period", ...shown.map((s) => s.name), "total", ...(prev ? ["previous_period"] : []), ...(bLine ? ["budget"] : [])];
    const rows = x.map((d, i) => [d, ...shown.map((s) => s.values[i] ?? 0), shown.reduce((a, s) => a + (s.values[i] ?? 0), 0),
      ...(prev ? [prev[i] ?? null] : []), ...(bLine ? [bLine[i] ?? null] : [])]);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([toCsv(cols, rows)], { type: "text/csv" }));
    a.download = `cost-analysis-${opts.dim}-${opts.gran}-${x[0] ?? ""}-${x[x.length - 1] ?? ""}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  }

  return (
    <div className={loading ? "loading" : ""} aria-busy={loading}>
      <div className="ca-bar">
        <label className="field">Saved views
          <select value="" onChange={(e) => { if (e.target.value) window.location.search = e.target.value; }}>
            <option value="">{views.data?.views.length ? "Open a saved view…" : "None saved yet"}</option>
            {(views.data?.views ?? []).map((v) => <option key={v.name} value={`?${v.query}`}>{v.name}</option>)}
          </select></label>
        <label className="field">Save this view as
          <span className="ca-inline"><input value={viewName} maxLength={60} placeholder="Name" onChange={(e) => setViewName(e.target.value)} />
            <button type="button" disabled={!viewName.trim()} onClick={() => void saveView()}>Save</button></span></label>
        {(views.data?.views.length ?? 0) > 0 && (
          <details className="ca-manage"><summary className="small">Manage</summary>
            <ul>{views.data!.views.map((v) => <li key={v.name}>{v.name} <button type="button" className="link-btn" onClick={() => void deleteView(v.name)}>Delete</button></li>)}</ul>
          </details>
        )}
      </div>

      <div className="ca-filters" role="group" aria-label="Filters for this chart only">
        <span className="ca-filters-label">Chart filters <span className="info" tabIndex={0} role="note"
          title="These apply to this chart only, on top of the dashboard filters, and are saved with a view. Where both filter the same field, only values in both count."
          aria-label="Chart filters apply to this chart only, on top of the dashboard filters.">i</span></span>
        {localDims.map((d) => (
          <span key={d} className="ca-filter">
            <MultiSelect dim={d} filters={{ ...filters, dims: { ...filters.dims, [d]: opts.dims[d] ?? [] } }} onChange={(v) => setLocal(d, v)} />
            <button type="button" className="ca-filter-x" aria-label={`Remove the ${DIM_LABEL[d]} chart filter`} title={`Remove the ${DIM_LABEL[d]} filter`}
              onClick={() => { setAdding((a) => a.filter((x) => x !== d)); setLocal(d, []); }}>×</button>
          </span>
        ))}
        <select className="ca-add" value="" aria-label="Add a chart filter"
          onChange={(e) => { const d = e.target.value as Dim; if (d) setAdding((a) => (a.includes(d) ? a : [...a, d])); }}>
          <option value="">+ Add filter</option>
          {DIMS.filter((d) => !localDims.includes(d)).map((d) => <option key={d} value={d}>{DIM_LABEL[d]}</option>)}
        </select>
        {localChips.length > 0 && (
          <ul className="chips" aria-label="Active chart filters">
            {localChips.map(({ d, v }) => (
              <li key={`${d}:${v}`}><span className="chip"><span className="muted">{DIM_LABEL[d]}:</span> {labelFor(v)}
                <button type="button" aria-label={`Remove chart filter ${DIM_LABEL[d]} ${labelFor(v)}`}
                  onClick={() => setLocal(d, (opts.dims[d] ?? []).filter((x) => x !== v))}>×</button></span></li>
            ))}
            <li><button type="button" className="link-btn" onClick={() => { setAdding([]); setHidden(new Set()); onOpts({ ...opts, dims: {} }); }}>Clear chart filters</button></li>
          </ul>
        )}
      </div>

      <div className="ca-bar">
        <label className="field">Group by
          <select value={opts.dim} onChange={(e) => { setHidden(new Set()); set({ dim: e.target.value as Dim }); }}>
            {DIMS.map((d) => <option key={d} value={d}>{DIM_LABEL[d]}</option>)}
          </select></label>
        <div className="seg" role="group" aria-label="Granularity">
          {GRANS.map((g) => <button key={g.id} type="button" aria-pressed={opts.gran === g.id} onClick={() => set({ gran: g.id })}>{g.label}</button>)}
        </div>
        <label className="field">Chart
          <select value={opts.chart} onChange={(e) => set({ chart: e.target.value as ChartKind })}>
            {CHARTS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select></label>
        <label className="check"><input type="checkbox" checked={opts.compare} onChange={(e) => set({ compare: e.target.checked })} /> Compare to previous period</label>
        <label className="check" title="Accumulated view only"><input type="checkbox" checked={opts.forecast} disabled={!acc} onChange={(e) => set({ forecast: e.target.checked })} /> Forecast to month end</label>
        <label className="check"><input type="checkbox" checked={opts.budget} onChange={(e) => set({ budget: e.target.checked })} /> Budget</label>
        <button type="button" onClick={download} disabled={!data?.available}>⤓ CSV</button>
      </div>

      {opts.compare && data?.comparison_reason && <p className="banner warn" role="status">{data.comparison_reason}</p>}
      {data?.available && (
        <div className="kpis">
          <Kpi label={`Total · ${periodLabel(filters.start, filters.end, x.length ? [`${(x[0] as string).slice(0, 7)}-01`, `${(x[x.length - 1] as string).slice(0, 7)}-01`] : [])}`}
            value={moneyShort(total, ccy)} full={money(total, ccy, 2)} note={hidden.size ? `${hidden.size} series hidden` : `${shown.length} series`} info={data.definition} />
          {opts.compare && data.previous && (
            <Kpi label="Previous period" value={moneyShort(prevTotal ?? 0, ccy)} full={money(prevTotal ?? 0, ccy, 2)}
              note={`${periodLabel(data.previous.start, data.previous.end, [])}${change !== null ? ` · ${change >= 0 ? "▲" : "▼"} ${moneyShort(Math.abs(change), ccy)}${prevTotal ? ` (${pct(Math.abs(change) / Math.abs(prevTotal))})` : ""}` : ""}`}
              info="All series, before any are hidden. For whole months, the same number of months before; otherwise the same number of days before." />
          )}
          {fc && monthEnd !== null && (
            <Kpi label="Forecast to month end" value={moneyShort(monthEnd, ccy)} full={money(monthEnd, ccy, 2)}
              note={`at ${moneyShort(fc.rate, ccy)} a day (last 7 days)`}
              info="The running total carried on to the end of the month at the average daily cost of the last 7 days with data. A projection, not a commitment: it assumes the recent pattern holds." />
          )}
          <Kpi label={`Monthly budget · ${scopeName}`} value={bAmt ? moneyShort(bAmt, ccy) : "not set"} full={bAmt ? money(bAmt, ccy, 2) : undefined}
            note={bAmt && oneMonth ? `${pct(total / bAmt)} used${monthEnd !== null ? ` · forecast ${pct(monthEnd / bAmt)}` : ""}` : bAmt ? "per month" : "set one below"}
            info="Budgets are per enrollment (filter to one enrollment) or for everything in the current currency, and are saved on this PC.">
            {budgetDraft === null
              ? <button type="button" className="link-btn small" onClick={() => setBudgetDraft(budget?.amount ?? "")}>{bAmt ? "Change" : "Set a budget"}</button>
              : (
                <span className="ca-inline">
                  <input inputMode="decimal" value={budgetDraft} placeholder={`Amount in ${ccy}`} onChange={(e) => setBudgetDraft(e.target.value)} aria-label={`Monthly budget in ${ccy}`} />
                  <button type="button" disabled={!(num(budgetDraft) > 0)} onClick={() => void saveBudget(budgetDraft)}>Save</button>
                  {budget && <button type="button" onClick={() => void saveBudget(null)}>Remove</button>}
                  <button type="button" className="link-btn" onClick={() => setBudgetDraft(null)}>Cancel</button>
                </span>
              )}
          </Kpi>
        </div>
      )}
      {fail && <p className="error small" role="alert">{fail}</p>}

      <section className="card" aria-label="Cost analysis chart">
        <header className="card-head">
          <h3>{acc ? "Accumulated" : grain === "day" ? "Daily" : "Monthly"} cost by {DIM_LABEL[opts.dim].toLowerCase()} ({ccy})</h3>
        </header>
        {!data && <p className="empty" aria-live="polite">Loading…</p>}
        {data && !data.available && <p className="muted small">{data.reason}</p>}
        {data?.available && (
          <>
            <AnalysisChart kind={opts.chart} x={x} xLabel={xLabel} series={shown} currency={ccy} previous={prev}
              previousLabel={data.previous ? `Previous (${periodLabel(data.previous.start, data.previous.end, [])})` : undefined}
              forecast={fc ? { x: fc.days, values: fc.values } : null} budget={bLine}
              onPick={(k) => onPick(opts.dim, k)} title={`${DIM_LABEL[opts.dim]} cost`} />
            <ul className="ca-legend" aria-label="Series: click a colour to hide or show it, a name to filter to it">
              {all.map((s) => (
                <li key={s.key} className={hidden.has(s.key) ? "off" : ""}>
                  <button type="button" className="ca-swatch" style={{ background: s.color }} aria-pressed={!hidden.has(s.key)}
                    title={hidden.has(s.key) ? "Show" : "Hide"}
                    onClick={() => setHidden((h) => { const n = new Set(h); n.has(s.key) ? n.delete(s.key) : n.add(s.key); return n; })} />
                  {s.key !== OTHER && s.key !== "(none)"
                    ? <button type="button" className="link-btn" onClick={() => onPick(opts.dim, s.key)} title={`Filter to ${s.name}`}>{s.name}</button>
                    : <span>{s.name}</span>}
                  <span className="muted">{moneyShort((data.series.find((x) => (x.key ?? "(none)") === s.key)?.total) ?? "0", ccy)}</span>
                </li>
              ))}
              {prev && <li><span className="ca-key ca-key-prev" /> Previous period</li>}
              {fc && <li><span className="ca-key ca-key-forecast" /> Forecast</li>}
              {bLine && <li><span className="ca-key ca-key-budget" /> Budget</li>}
            </ul>
          </>
        )}
      </section>
      <p className="muted small">
        Uses the basis, currency, period and dashboard filters, plus any chart filters set here (these affect this chart only). The 8 largest values are shown on their own and the rest are added up as Others.
        Click a colour in the legend to hide a series, or a name to filter the whole dashboard to it. Saved views and budgets are stored on this PC.
      </p>
      <TimingNote timing={timing} loading={loading} />
    </div>
  );
}
