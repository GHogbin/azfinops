// Filters + view live in the URL query string so a view can be bookmarked and survives a reload.
import { DIMS, filterParams, type CostKind, type Dim, type Filters } from "./api";

export type View = "overview" | "explorer" | "analysis" | "spend" | "applications" | "optimization" | "ai" | "pricing" | "commitments" | "data" | "knowledge";
const VIEWS: readonly View[] = ["overview", "explorer", "analysis", "spend", "applications", "optimization", "ai", "pricing", "commitments", "data", "knowledge"];

/** Cost analysis chart settings, kept in the URL with the filters so a view can be bookmarked and saved. */
export type Granularity = "day" | "month" | "acc";
export type ChartKind = "area" | "column" | "grouped" | "line" | "donut" | "table";
export interface AnalysisOpts {
  dim: Dim; gran: Granularity; chart: ChartKind; compare: boolean; forecast: boolean; budget: boolean;
  /** Filters that apply to the Cost analysis chart only, on top of the dashboard filters. Kept in the URL as af.<dimension>. */
  dims: Filters["dims"];
}
export const DEFAULT_ANALYSIS: AnalysisOpts = { dim: "service", gran: "acc", chart: "area", compare: false, forecast: true, budget: true, dims: {} };
const GRANS: readonly Granularity[] = ["day", "month", "acc"];
const CHARTS: readonly ChartKind[] = ["area", "column", "grouped", "line", "donut", "table"];

function analysisFrom(p: URLSearchParams): AnalysisOpts {
  const d = p.get("ag"); const g = p.get("gr"); const c = p.get("ch");
  const flag = (k: string, dflt: boolean) => (p.get(k) === null ? dflt : p.get(k) === "1");
  const dims: Filters["dims"] = {};
  for (const x of DIMS) {
    const vals = p.getAll(`af.${x}`).filter((v) => v.length > 0 && v.length <= 200).slice(0, MAX_VALUES);
    if (vals.length) dims[x] = vals;
  }
  return {
    dim: DIMS.find((x) => x === d) ?? DEFAULT_ANALYSIS.dim,
    gran: GRANS.find((x) => x === g) ?? DEFAULT_ANALYSIS.gran,
    chart: CHARTS.find((x) => x === c) ?? DEFAULT_ANALYSIS.chart,
    compare: flag("cmp", DEFAULT_ANALYSIS.compare), forecast: flag("fc", DEFAULT_ANALYSIS.forecast), budget: flag("bud", DEFAULT_ANALYSIS.budget),
    dims,
  };
}
const ISO = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;
const MAX_VALUES = 200;

export function toSearch(f: Filters, view: View, group?: Dim | null, a?: AnalysisOpts): string {
  const p = filterParams(f);
  p.set("view", view);
  if (view === "explorer" && group) p.set("group", group);
  if (view === "analysis" && a) {
    p.set("ag", a.dim); p.set("gr", a.gran); p.set("ch", a.chart);
    p.set("cmp", a.compare ? "1" : "0"); p.set("fc", a.forecast ? "1" : "0"); p.set("bud", a.budget ? "1" : "0");
    for (const x of DIMS) for (const v of a.dims[x] ?? []) p.append(`af.${x}`, v);
  }
  return `?${p.toString()}`;
}

/** Parse untrusted query-string input; anything invalid is dropped, never passed through. */
export function fromSearch(search: string): { filters: Filters | null; view: View; group: Dim | null; analysis: AnalysisOpts } {
  const p = new URLSearchParams(search);
  const requested = p.get("view");
  const view: View = VIEWS.find((v) => v === requested) ?? "overview";
  const g = p.get("group");
  const group: Dim | null = DIMS.find((d) => d === g) ?? null;
  const kind = p.get("cost_kind");
  const ccy = p.get("currency");
  if ((kind !== "actual" && kind !== "amortized") || !ccy || !/^[A-Z]{3}$/.test(ccy)) {
    return { filters: null, view, group, analysis: analysisFrom(p) };
  }
  const start = p.get("start");
  const end = p.get("end");
  const dims: Filters["dims"] = {};
  for (const d of DIMS) {
    const vals = p.getAll(d).filter((v) => v.length > 0 && v.length <= 200).slice(0, MAX_VALUES);
    if (vals.length) dims[d] = vals;
  }
  return {
    filters: {
      costKind: kind as CostKind, currency: ccy, ...(p.get("combined") === "1" ? { combined: true } : {}),
      start: start && ISO.test(start) ? start : null, end: end && ISO.test(end) ? end : null, dims,
    },
    view,
    group,
    analysis: analysisFrom(p),
  };
}
