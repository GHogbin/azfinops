import { pct } from "../format";

let names: Record<string, string> = {};
let order: string[] = [];

/** Registers the enrollment names the API knows (from the export's BillingAccountName). */
export function setAccountNames(n: Record<string, string> | undefined): void {
  names = n ?? {};
}

/** Registers every enrollment ID, so each one keeps the same colour on every card and page. */
export function setAccounts(ids: string[] | undefined): void {
  order = [...new Set(ids ?? [])].sort();
}

/** The enrollment's name when known, otherwise "Enrollment <number>". */
export function acctName(a: string): string {
  return names[a] ?? (/^\d+$/.test(a) ? `Enrollment ${a}` : a);
}

const SLOTS = 4;

/** A colour slot from the enrollment's position in the sorted ID list: stable across pages and reloads. */
export function enrollmentSlot(a: string): number {
  const i = order.indexOf(a);
  if (i >= 0) return (i % SLOTS) + 1;
  let h = 0;                                   // an ID the meta call did not list: still stable, from its text
  for (const ch of a) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return (h % SLOTS) + 1;
}

export function enrollmentColor(a: string): string {
  return `var(--enr-${enrollmentSlot(a)})`;
}

export function Dot({ account }: { account: string }) {
  return <span className="enr-dot" aria-hidden="true" style={{ background: enrollmentColor(account) }} />;
}

export interface AmountItem {
  account: string;
  /** The amount as a number, used for the bar. */
  value: number;
  /** The amount as it should be read. */
  text: string;
  currency?: string;
}

/** Segment widths (0–100) for amounts that add up. Negative amounts take no width; they stay in the legend. */
export function segmentWidths(values: number[]): number[] {
  const pos = values.reduce((a, v) => a + Math.max(0, v), 0);
  return values.map((v) => (pos <= 0 ? 0 : (Math.max(0, v) / pos) * 100));
}

/**
 * Per-enrollment split of an amount that adds up (cost, usage, rows): one thin stacked bar plus a legend.
 * Amounts in different currencies cannot be stacked, so then only the legend is shown.
 */
export function SplitBar({ items, label }: { items: AmountItem[]; label: string }) {
  if (items.length < 2) return null;
  const sameCurrency = new Set(items.map((i) => i.currency ?? "")).size === 1;
  const widths = segmentWidths(items.map((i) => i.value));
  const drawable = sameCurrency && widths.some((w) => w > 0);
  return (
    <div className="splitbar" role="group" aria-label={label}>
      {drawable && (
        <div className="splitbar-track" role="img"
          aria-label={`${label}: ${items.map((i, k) => `${acctName(i.account)} ${i.text} (${pct((widths[k] ?? 0) / 100, 0)})`).join(", ")}`}>
          {items.map((i, k) => (widths[k] ?? 0) > 0 && (
            <span key={i.account} title={`${acctName(i.account)}: ${i.text}`}
              style={{ width: `${widths[k]}%`, background: enrollmentColor(i.account) }} />
          ))}
        </div>
      )}
      <ul className="splitbar-legend">
        {items.map((i) => (
          <li key={i.account}><Dot account={i.account} /><span className="muted">{acctName(i.account)}</span><span>{i.text}</span></li>
        ))}
      </ul>
    </div>
  );
}

export interface RatioItem {
  account: string;
  /** 0–1, or null when the ratio does not apply to this enrollment. */
  value: number | null;
}

/** Per-enrollment split of a ratio (utilization, coverage, attribution): a marker per enrollment on a 0–100% track. */
export function RatioSplit({ items, label, track = true }: { items: RatioItem[]; label: string; track?: boolean }) {
  if (items.length < 2) return null;
  const shown = items.filter((i) => (track ? i.value !== null : false));
  return (
    <div className="splitbar" role="group" aria-label={label}>
      {shown.length > 0 && (
        <div className="ratio-track" role="img"
          aria-label={`${label}: ${items.map((i) => `${acctName(i.account)} ${i.value === null ? "n/a" : pct(i.value)}`).join(", ")}`}>
          {shown.map((i) => (
            <span key={i.account} className="ratio-mark" title={`${acctName(i.account)}: ${pct(i.value)}`}
              style={{ left: `${Math.min(100, Math.max(0, (i.value as number) * 100))}%`, background: enrollmentColor(i.account) }} />
          ))}
        </div>
      )}
      <ul className="splitbar-legend">
        {items.map((i) => (
          <li key={i.account}><Dot account={i.account} /><span className="muted">{acctName(i.account)}</span><span>{i.value === null ? "n/a" : pct(i.value)}</span></li>
        ))}
      </ul>
    </div>
  );
}

/** Per-enrollment text lines for figures that are neither amounts nor ratios. */
export function Split({ items, label }: { items: { account: string; text: string }[]; label: string }) {
  if (items.length < 2) return null;
  return (
    <ul className="splitbar-legend split-lines" aria-label={label}>
      {items.map((i) => (<li key={i.account}><Dot account={i.account} /><span className="muted">{acctName(i.account)}</span><span>{i.text}</span></li>))}
    </ul>
  );
}

/** Only the stacked track of a SplitBar, for places that already show their own legend. */
export function StackTrack({ items, label }: { items: AmountItem[]; label: string }) {
  const widths = segmentWidths(items.map((i) => i.value));
  if (items.length < 2 || !widths.some((w) => w > 0)) return null;
  return (
    <div className="splitbar-track" role="img"
      aria-label={`${label}: ${items.map((i, k) => `${acctName(i.account)} ${i.text} (${pct((widths[k] ?? 0) / 100, 0)})`).join(", ")}`}>
      {items.map((i, k) => (widths[k] ?? 0) > 0 && (
        <span key={i.account} title={`${acctName(i.account)}: ${i.text}`} style={{ width: `${widths[k]}%`, background: enrollmentColor(i.account) }} />
      ))}
    </div>
  );
}
