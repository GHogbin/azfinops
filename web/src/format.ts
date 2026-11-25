// Presentation helpers. Rounding happens here and only here.

export function num(v: string | number | null | undefined): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

export function money(v: string | number, currency: string, maximumFractionDigits = 0): string {
  // a tiny negative that rounds to zero must read £0, not "-£0"
  const rounded = Number(num(v).toFixed(maximumFractionDigits));
  const x = rounded === 0 ? 0 : num(v);
  try {
    return new Intl.NumberFormat("en-GB", {
      // narrowSymbol: "$" rather than "US$" (en-GB would otherwise mark US dollars)
      style: "currency", currency, currencyDisplay: "narrowSymbol", maximumFractionDigits, minimumFractionDigits: maximumFractionDigits,
    }).format(x);
  } catch {
    return `${currency} ${x.toFixed(maximumFractionDigits)}`;
  }
}

/** The currency's own symbol: £ for GBP, $ for USD, € for EUR. Falls back to the code when there is none. */
export function currencySymbol(currency: string): string {
  try {
    const part = new Intl.NumberFormat("en-GB", { style: "currency", currency, currencyDisplay: "narrowSymbol" })
      .formatToParts(0).find((x) => x.type === "currency");
    return part?.value ?? currency;
  } catch {
    return currency;
  }
}

/** Headline form for tiles: £2.22M, £44.9K. Falls back to the full form if the currency is not known. */
export function moneyShort(v: string | number, currency: string): string {
  try {
    // en-GB compact writes "m" and "bn"; tiles read better as M and B (k stays lower case)
    return new Intl.NumberFormat("en-GB", { style: "currency", currency, currencyDisplay: "narrowSymbol", notation: "compact", maximumFractionDigits: 2 })
      .format(num(v)).replace(/bn$/, "B").replace(/m$/, "M").replace(/K$/, "k");
  } catch {
    return money(v, currency);
  }
}

/** Short axis/label form: 1.2M, 340k. */
export function compact(n: number): string {
  const a = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (a >= 1e9) return `${sign}${(a / 1e9).toFixed(a >= 1e10 ? 0 : 1)}B`;
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`;
  return `${sign}${a.toFixed(a >= 100 || Number.isInteger(a) ? 0 : 1)}`;
}

export function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(digits)}%`;
}

export function count(n: number): string {
  return new Intl.NumberFormat("en-GB").format(n);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-09-02" -> "2 Sep 2026" (no Date parsing: avoids timezone shifts). */
export function dateLabel(iso: string, withYear = true): string {
  const [y, m, d] = iso.split("-");
  const mi = Number(m) - 1;
  if (!y || !d || mi < 0 || mi > 11) return iso;
  return `${Number(d)} ${MONTHS[mi]}${withYear ? ` ${y}` : ""}`;
}

export function monthLabel(iso: string): string {
  const [y, m] = iso.split("-");
  const mi = Number(m) - 1;
  return y && mi >= 0 && mi < 12 ? `${MONTHS[mi]} ${y}` : iso;
}

/** UTC milliseconds for a YYYY-MM-DD string. */
export function dayMs(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

/** Round tick values spanning [min, max] (about `target` of them). */
export function niceTicks(min: number, max: number, target = 5): number[] {
  if (!(max > min)) return [min];
  const raw = (max - min) / Math.max(1, target);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step * 1e-9; t += step) out.push(Math.round(t / step) * step);
  return out;
}

export function lastDayOfMonth(isoMonthStart: string): string {
  const [y, m] = isoMonthStart.split("-").map(Number);
  const d = new Date(Date.UTC(y ?? 1970, m ?? 1, 0)).getUTCDate();
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** Month starts (YYYY-MM-01) covered by an inclusive date range, oldest first. */
export function monthsBetween(firstIso: string, lastIso: string): string[] {
  const [fy, fm] = firstIso.split("-").map(Number);
  const [ly, lm] = lastIso.split("-").map(Number);
  if (!fy || !fm || !ly || !lm) return [];
  const out: string[] = [];
  for (let y = fy, m = fm; y < ly || (y === ly && m <= lm); m === 12 ? (y++, m = 1) : m++) {
    out.push(`${y}-${String(m).padStart(2, "0")}-01`);
    if (out.length > 600) break;
  }
  return out;
}

const MONTH_SHORT = (iso: string): string => monthLabel(iso).split(" ")[0] ?? iso;

/** True when start..end is exactly whole calendar months. */
function wholeMonths(start: string, end: string): boolean {
  return start.endsWith("-01") && end === lastDayOfMonth(`${end.slice(0, 7)}-01`);
}

/**
 * What a figure covers, in words: "Sep 2026", "Aug–Sep 2026", "Nov 2026–Jan 2027" or "3 Aug – 17 Sep 2026".
 * Open ends fall back to the first and last month that has data.
 */
export function periodLabel(start: string | null, end: string | null, months: string[]): string {
  const s = start ?? months[0] ?? null;
  const e = end ?? (months.length ? lastDayOfMonth(months[months.length - 1] as string) : null);
  if (!s || !e) return "";
  if (wholeMonths(s, e)) {
    if (s.slice(0, 7) === e.slice(0, 7)) return monthLabel(s);
    return s.slice(0, 4) === e.slice(0, 4)
      ? `${MONTH_SHORT(s)}–${monthLabel(e)}`
      : `${monthLabel(s)}–${monthLabel(e)}`;
  }
  return s.slice(0, 4) === e.slice(0, 4) ? `${dateLabel(s, false)} – ${dateLabel(e)}` : `${dateLabel(s)} – ${dateLabel(e)}`;
}

/** The newest calendar month fully inside first..last, as {start, end}; null when there is none. */
export function latestCompleteMonth(first: string, last: string): { start: string; end: string } | null {
  let m = `${last.slice(0, 7)}-01`;
  if (lastDayOfMonth(m) !== last) m = previousMonthStart(m);
  return m >= first ? { start: m, end: lastDayOfMonth(m) } : null;
}

function previousMonthStart(monthStart: string): string {
  const [y, mo] = monthStart.split("-").map(Number);
  return mo === 1 ? `${(y ?? 1970) - 1}-12-01` : `${y}-${String((mo ?? 1) - 1).padStart(2, "0")}-01`;
}

/** The month before start..end when that range is exactly one calendar month; otherwise null. */
export function previousMonth(start: string | null, end: string | null): { start: string; end: string } | null {
  if (!start || !end || !wholeMonths(start, end) || start.slice(0, 7) !== end.slice(0, 7)) return null;
  const p = previousMonthStart(start);
  return { start: p, end: lastDayOfMonth(p) };
}

export type PeriodId = "latest" | "mtd" | "last3" | "ytd" | "all";
export interface PeriodPreset { id: PeriodId; label: string; start: string | null; end: string | null; note?: string }

/**
 * The period buttons, worked out from the data rather than today's date.
 * The complete-month list is validated for every selected enrollment and native currency by the API.
 */
export function periodPresets(first: string, lastAny: string, completeMonths: string[]): PeriodPreset[] {
  const out: PeriodPreset[] = [];
  const lastComplete = completeMonths[completeMonths.length - 1];
  const latest = lastComplete ? { start: lastComplete, end: lastDayOfMonth(lastComplete) } : null;
  if (latest) {
    out.push({ id: "latest", label: "Latest month", ...latest, note: monthLabel(latest.start) });
    const months = monthsBetween(first, latest.end);
    const recent: string[] = [];
    for (const month of [...months].reverse().slice(0, 3)) {
      if (!completeMonths.includes(month)) break;
      recent.unshift(month);
    }
    const three = recent[0];
    if (three && recent.length >= 2) out.push({ id: "last3", label: `Last ${recent.length} months`, start: three, end: latest.end });
    const jan = `${latest.start.slice(0, 4)}-01-01`;
    const ytdStart = jan < first ? `${first.slice(0, 7)}-01` : jan;
    const dup = out.some((x) => x.start === ytdStart && x.end === latest.end);    // same range as a button already offered
    if (ytdStart !== latest.start && !dup && monthsBetween(ytdStart, latest.end).every((m) => completeMonths.includes(m))) {
      out.push({ id: "ytd", label: "Year to date", start: ytdStart, end: latest.end });
    }
  }
  const mStart = `${lastAny.slice(0, 7)}-01`;
  if (lastAny !== lastDayOfMonth(mStart) && mStart >= `${first.slice(0, 7)}-01`) {
    out.push({ id: "mtd", label: "Month to date", start: mStart < first ? first : mStart, end: lastAny, note: "partial" });
  }
  out.push({ id: "all", label: "All", start: null, end: null });
  return out;
}
