import { useCallback, useEffect, useState } from "react";
import type { Dim, Filters, Meta } from "./api";
import { ErrorBox } from "./components/Status";
import { Commitments } from "./components/Commitments";
import { Explorer } from "./components/Explorer";
import { FilterBar } from "./components/FilterBar";
import { Overview } from "./components/Overview";
import { Applications } from "./components/Applications";
import { AiDatabase } from "./components/AiDatabase";
import { Optimization } from "./components/Optimization";
import { DataManagement } from "./components/DataManagement";
import { setAccountNames, setAccounts } from "./components/EnrollmentSplit";
import { QualityChip } from "./components/QualityChip";
import { ActivityButton } from "./components/ActivityButton";
import { CombinedNote } from "./components/CombinedNote";
import { Knowledge } from "./components/Knowledge";
import { Pricing } from "./components/Pricing";
import { Spend } from "./components/Spend";
import { lastDayOfMonth } from "./format";
import { completeMonths, PeriodPicker } from "./components/PeriodPicker";
import { CostAnalysis } from "./components/CostAnalysis";
import { useApi } from "./hooks";
import { fromSearch, toSearch, type AnalysisOpts, type View } from "./state";

type Theme = "system" | "light" | "dark" | "glass";

type Group = "Analyse" | "Save" | "Reference";
const WORKSPACES: { id: View; label: string; short: string; group: Group }[] = [
  { id: "overview", label: "Portfolio Overview", short: "Overview", group: "Analyse" },
  { id: "explorer", label: "Cost Explorer", short: "Explorer", group: "Analyse" },
  { id: "analysis", label: "Cost analysis", short: "Cost analysis", group: "Analyse" },
  { id: "spend", label: "Spend / Charge Analysis", short: "Spend", group: "Analyse" },
  { id: "applications", label: "Applications", short: "Applications", group: "Analyse" },
  { id: "ai", label: "AI & Database", short: "AI & Database", group: "Analyse" },
  { id: "commitments", label: "Commitments & Savings", short: "Commitments", group: "Save" },
  { id: "optimization", label: "Optimization", short: "Optimization", group: "Save" },
  { id: "pricing", label: "Pricing & Reference Data", short: "Pricing", group: "Reference" },
  { id: "data", label: "Data Management", short: "Data", group: "Reference" },
  { id: "knowledge", label: "Knowledge", short: "Knowledge", group: "Reference" },
];
const GROUPS: Group[] = ["Analyse", "Save", "Reference"];

/** The next level to group by when a value is opened in the Explorer. */
const DEEPER: Partial<Record<Dim, Dim>> = {
  service: "meter_subcategory", subscription: "resource_group", resource_group: "service", application: "subscription",
  billing_account: "subscription", meter_subcategory: "region", environment: "application", region: "service",
};

function ThemeIcon({ theme }: { theme: Theme }) {
  const p = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (theme === "light") return <svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>;
  if (theme === "dark") return <svg {...p}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>;
  if (theme === "system") return <svg {...p}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8m-4-4v4" /></svg>;
  return <svg {...p}><path d="M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5z" /></svg>;
}

const PAGE_TITLES: Record<View, [string, string]> = {
  overview: ["Portfolio overview", "Where the money goes and how it is changing."],
  explorer: ["Cost Explorer", "Group and filter cost by any dimension."],
  analysis: ["Cost analysis", "One chart you reshape: group by, daily, monthly or accumulated, chart type, forecast, budget and the period before."],
  spend: ["Spend / Charge Analysis", "What the bill is made of, and why it moved between months."],
  commitments: ["Commitments & Savings", "Reservations and savings plans: use, coverage and waste."],
  optimization: ["Optimization", "Resources rightsized or decommissioned since the month before."],
  applications: ["Applications", "Cost by application, and what cannot be tied to one."],
  ai: ["AI & Database", "AI, data and database spend."],
  pricing: ["Pricing & Reference Data", "Pricing models, price sheets and reference data."],
  data: ["Data Management", "Import files, see what is in the database, and whether it can be trusted."],
  knowledge: ["Knowledge", "What each workspace shows and how every figure is calculated."],
};

function readTheme(): Theme {
  try {
    const t = localStorage.getItem("azfinops.theme.v2");
    return t === "light" || t === "dark" || t === "system" || t === "glass" ? t : "glass";
  } catch {
    return "glass"; // storage can be blocked; the app must still work
  }
}

function applyTheme(t: Theme): void {
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}

function defaultFilters(meta: Meta): Filters | null {
  const best = [...meta.scopes].sort((a, b) =>
    Number(b.cost_kind === "amortized") - Number(a.cost_kind === "amortized") || b.rows - a.rows)[0];
  if (!best) return null;
  // When the cost basis has several currencies (one per enrollment), open on the combined view so every
  // enrollment is included; USD is the reporting currency if it is present.
  const same = meta.scopes.filter((x) => x.cost_kind === best.cost_kind);
  const currencies = [...new Set(same.map((x) => x.currency))];
  const combined = currencies.length > 1;
  // Open on the newest month every included enrollment covers in full, so the headline is one complete month.
  const initial: Filters = { costKind: best.cost_kind,
    currency: combined && currencies.includes("USD") ? "USD" : best.currency,
    combined, start: null, end: null, dims: {} };
  const months = completeMonths(meta, initial);
  const latest = months[months.length - 1];
  return latest ? { ...initial, start: latest, end: lastDayOfMonth(latest) } : initial;
}

export default function App() {
  const initial = fromSearch(window.location.search);
  const meta = useApi<Meta>("/api/meta", null);
  const [filters, setFilters] = useState<Filters | null>(initial.filters);
  const [view, setView] = useState<View>(initial.view);
  const [group, setGroup] = useState<Dim | null>(initial.group);
  const [analysis, setAnalysis] = useState<AnalysisOpts>(initial.analysis);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [filtersOpen, setFiltersOpen] = useState(false);

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    if (!filters && meta.data) setFilters(defaultFilters(meta.data));
  }, [filters, meta.data]);
  useEffect(() => {
    if (filters) window.history.replaceState(null, "", toSearch(filters, view, group, analysis));
  }, [filters, view, group, analysis]);

  const cycleTheme = () => {
    const next: Theme = theme === "glass" ? "light" : theme === "light" ? "dark" : theme === "dark" ? "system" : "glass";
    setTheme(next);
    try { localStorage.setItem("azfinops.theme.v2", next); } catch { /* ignore */ }
  };
  const pick = useCallback((dim: Dim, value: string) => {
    setFilters((f) => {
      if (!f) return f;
      const cur = f.dims[dim] ?? [];
      return cur.includes(value) ? f : { ...f, dims: { ...f.dims, [dim]: [...cur, value] } };
    });
  }, []);
  /** Filters to a value and opens it in the Explorer, grouped one level deeper. */
  const explore = useCallback((dim: Dim, value: string) => {
    pick(dim, value);
    setGroup(DEEPER[dim] ?? "service");
    setView("explorer");
    window.scrollTo({ top: 0 });
  }, [pick]);
  const onGroup = useCallback((d: Dim) => setGroup(d), []);
  const current = WORKSPACES.find((w) => w.id === view);

  setAccountNames(meta.data?.billing_account_names);   // names and colours must be known before the cards below render
  setAccounts(meta.data?.billing_accounts);
  const showFilters = !!(meta.data && filters && view !== "data" && view !== "knowledge");
  const activeFilters = filters ? Object.values(filters.dims).reduce((n, v) => n + (v?.length ?? 0), 0) : 0;
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19V9m6 10V5m6 14v-7m4 7H2" /></svg>
          </span>
          <div><h1>Azure FinOps</h1><span className="muted small">local · read-only</span></div>
        </div>
        <nav className="tabs" aria-label="Workspaces">
          {GROUPS.map((g) => (
            <div className="tab-group" key={g} role="group" aria-label={g}>
              <span className="tab-group-label" aria-hidden="true">{g}</span>
              {WORKSPACES.filter((w) => w.group === g).map((w) => (
                <button key={w.id} type="button" role="link" title={w.label} aria-label={w.label} aria-current={view === w.id ? "page" : undefined}
                  className={view === w.id ? "tab active" : "tab"} onClick={() => setView(w.id)}>{w.short}</button>
              ))}
            </div>
          ))}
        </nav>
        <label className="tab-menu">
          <span className="sr-only">Workspace</span>
          <select value={view} onChange={(e) => setView(e.target.value as View)}>
            {GROUPS.map((g) => (
              <optgroup key={g} label={g}>
                {WORKSPACES.filter((w) => w.group === g).map((w) => <option key={w.id} value={w.id}>{w.label}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
        <div className="bar-actions">
          <ActivityButton />
          <button type="button" className="icon-btn theme-btn" onClick={cycleTheme} aria-label={`Colour theme: ${theme}. Click to change.`} title={`Theme: ${theme}`}>
            <ThemeIcon theme={theme} />
          </button>
        </div>
      </header>
      {meta.data && filters && (
        <div className="scopebar" aria-label="Current scope">
          {showFilters && (
            <>
              <PeriodPicker meta={meta.data} filters={filters} onChange={setFilters} />
              <CombinedNote filters={filters} />
              <button type="button" className="filters-btn" aria-expanded={filtersOpen} aria-controls="filter-drawer"
                onClick={() => setFiltersOpen((o) => !o)}>Filters{activeFilters > 0 ? ` (${activeFilters})` : ""} {filtersOpen ? "▴" : "▾"}</button>
            </>
          )}
          {!showFilters && <span className="muted small">{current?.label}</span>}
          {meta.data.scopes.length > 0 && <QualityChip onOpen={() => setView("data")} />}
        </div>
      )}
      {showFilters && filtersOpen && meta.data && filters && (
        <div id="filter-drawer" className="filter-drawer">
          <FilterBar meta={meta.data} filters={filters} onChange={setFilters} />
        </div>
      )}
      <main className="content">
        {meta.data?.recovery_required && (
          <p className="banner error" role="alert">{meta.data.recovery_message}</p>
        )}
        {view !== "overview" && <header className="page-head">
          <div>
            <h2>{PAGE_TITLES[view][0]}</h2>
            <p className="muted">{PAGE_TITLES[view][1]}</p>
          </div>
        </header>}
        {meta.error && <ErrorBox message={meta.error} onRetry={meta.reload} />}
        {!meta.data && !meta.error && <p className="empty" aria-live="polite">Connecting to the local API…</p>}
        {meta.data && meta.data.scopes.length === 0 && (
          <p className="banner warn">No data has been imported yet. Run <code>azfinops-import</code> and <code>azfinops-aggregate</code>, then reload.</p>
        )}
        {meta.data && view === "data" && <DataManagement />}
        {view === "knowledge" && <Knowledge />}
        {meta.data && filters && view !== "data" && view !== "knowledge" && (
          <>
            {view === "overview" && <Overview filters={filters} onPick={pick} onOpen={setView} onExplore={explore} />}
            {view === "explorer" && <Explorer filters={filters} onPick={pick} group={group} onGroup={onGroup} />}
            {view === "analysis" && <CostAnalysis filters={filters} opts={analysis} onOpts={setAnalysis} onPick={pick} />}
            {view === "spend" && <Spend filters={filters} meta={meta.data} />}
            {view === "applications" && <Applications filters={filters} />}
            {view === "optimization" && <Optimization filters={filters} />}
            {view === "ai" && <AiDatabase filters={filters} />}
            {view === "pricing" && <Pricing filters={filters} />}
            {view === "commitments" && <Commitments filters={filters} />}
          </>
        )}
      </main>
    </div>
  );
}
