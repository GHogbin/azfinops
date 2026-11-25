/** Pure helpers for the Cost analysis chart: accumulation, the month-end forecast, the budget line, the previous
 * period. Days are ISO strings (YYYY-MM-DD). */
import { lastDayOfMonth } from "./format";

export function accumulate(values: number[]): number[] {
  let run = 0;
  return values.map((v) => (run += v));
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Days after `last` up to the end of its month (empty when `last` is the last day). */
export function daysToMonthEnd(last: string): string[] {
  const end = lastDayOfMonth(`${last.slice(0, 7)}-01`);
  const out: string[] = [];
  for (let d = addDays(last, 1); d <= end; d = addDays(d, 1)) out.push(d);
  return out;
}

/**
 * Accumulated forecast to the end of the last month: the running total carries on at the average daily cost of the
 * last `window` days. Returns the extra days and the projected running total on each; nothing when the month is
 * already complete, or the range was cut before month end.
 */
export function forecast(days: string[], daily: number[], rangeEnd: string | null, window = 7): { days: string[]; values: number[]; rate: number } | null {
  const last = days[days.length - 1];
  if (!last || daily.length === 0) return null;
  const extra = daysToMonthEnd(last).filter((d) => rangeEnd === null || d <= rangeEnd);
  if (extra.length === 0) return null;
  const recent = daily.slice(-window);
  const rate = recent.reduce((a, v) => a + v, 0) / recent.length;
  const start = daily.reduce((a, v) => a + v, 0);
  return { days: extra, values: extra.map((_d, i) => start + rate * (i + 1)), rate };
}

/**
 * The budget per period for a monthly budget: per day = budget / days in that month, per month = budget, and for an
 * accumulated chart the budget of every month reached so far (flat within a month, so the line shows the month's
 * limit the running total must stay under).
 */
export function budgetLine(periods: string[], gran: "day" | "month" | "acc", monthly: number): number[] {
  if (gran === "month") return periods.map(() => monthly);
  const dim = (iso: string) => Number(lastDayOfMonth(`${iso.slice(0, 7)}-01`).slice(8, 10));
  if (gran === "day") return periods.map((d) => monthly / dim(d));
  const months: string[] = [];
  return periods.map((d) => {
    const m = d.slice(0, 7);
    if (!months.includes(m)) months.push(m);
    return monthly * months.length;
  });
}

/** The previous period's totals on this period's x positions (day 1 against day 1), accumulated when asked. */
export function alignPrevious(prev: number[], length: number, acc: boolean): (number | null)[] {
  const v = acc ? accumulate(prev) : prev;
  return Array.from({ length }, (_x, i) => (i < v.length ? (v[i] as number) : null));
}

/**
 * The dashboard filters with the Cost analysis chart's own filters added. For a dimension filtered in both places
 * only values in both are kept; when none are, nothing matches (the chart shows no cost rather than ignoring a filter).
 */
export function withLocalFilters<F extends { dims: Partial<Record<string, string[]>> }>(f: F, local: Partial<Record<string, string[]>>): F {
  const dims: Partial<Record<string, string[]>> = { ...f.dims };
  for (const [d, vals] of Object.entries(local)) {
    if (!vals || vals.length === 0) continue;
    const g = f.dims[d];
    if (!g || g.length === 0) { dims[d] = vals; continue; }
    const both = vals.filter((v) => g.includes(v));
    dims[d] = both.length ? both : [NO_MATCH];
  }
  return { ...f, dims };
}
export const NO_MATCH = "__no_match__";
