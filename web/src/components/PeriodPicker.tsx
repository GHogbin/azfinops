import { useEffect, useRef, useState } from "react";
import type { Filters, Meta } from "../api";
import { periodLabel, periodPresets } from "../format";

export function completeMonths(meta: Meta, f: Filters): string[] {
  const accounts = f.dims.billing_account ?? [];
  const rows = (meta.scope_months ?? []).filter((x) => x.cost_kind === f.costKind
    && (f.combined || x.currency === f.currency) && (!accounts.length || accounts.includes(x.account)));
  const scopes = new Set(rows.map((x) => `${x.account}|${x.currency}`));
  const months = [...new Set(rows.map((x) => x.month))].sort();
  return months.filter((m) => {
    const present = rows.filter((x) => x.month === m);
    return present.length === scopes.size && present.every((x) => x.complete);
  });
}

/** The data's date span for the current basis and currency (every currency in the Combined view). */
export function scopeSpan(meta: Meta, f: Filters): { first: string; lastAll: string; lastAny: string } | null {
  const s = meta.scopes.filter((x) => x.cost_kind === f.costKind && (f.combined || x.currency === f.currency));
  if (s.length === 0) return null;
  const firsts = s.map((x) => x.first_date).sort();
  const lasts = s.map((x) => x.last_date).sort();
  return { first: firsts[0] as string, lastAll: lasts[0] as string, lastAny: lasts[lasts.length - 1] as string };
}

/** Period buttons in the scope bar, with Custom for any two dates. Writes the same start and end as the Filters drawer. */
export function PeriodPicker({ meta, filters, onChange }: { meta: Meta; filters: Filters; onChange: (f: Filters) => void }) {
  const span = scopeSpan(meta, filters);
  const [custom, setCustom] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!custom) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setCustom(false); };
    const esc = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") setCustom(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [custom]);
  if (!span) return null;
  const presets = periodPresets(span.first, span.lastAny, completeMonths(meta, filters));
  const active = presets.find((p) => p.start === filters.start && p.end === filters.end);
  const set = (start: string | null, end: string | null) => onChange({ ...filters, start, end });
  const bad = !!filters.start && !!filters.end && filters.start > filters.end;
  return (
    <div className="period" ref={box}>
      <div className="seg period-seg" role="group" aria-label="Period">
        {presets.map((p) => (
          <button key={p.id} type="button" aria-pressed={active?.id === p.id} onClick={() => { set(p.start, p.end); setCustom(false); }}
            title={p.start ? `${periodLabel(p.start, p.end, [])}${p.note === "partial" ? " (partial month)" : ""}` : "Every date with data"}>
            {p.label}{p.note === "partial" && <span className="muted small"> · partial</span>}
          </button>
        ))}
        <button type="button" aria-pressed={!active} aria-expanded={custom} onClick={() => setCustom((c) => !c)}>
          {active ? "Custom" : periodLabel(filters.start, filters.end, []) || "Custom"} ▾
        </button>
      </div>
      {custom && (
        <div className="popover period-pop" role="dialog" aria-label="Custom period">
          <label className="field">From
            <input type="date" value={filters.start ?? ""} min={span.first} max={span.lastAny} onChange={(e) => set(e.target.value || null, filters.end)} /></label>
          <label className="field">To
            <input type="date" value={filters.end ?? ""} min={span.first} max={span.lastAny} onChange={(e) => set(filters.start, e.target.value || null)} /></label>
          {bad && <p className="muted small" role="alert">The start is after the end.</p>}
          <p className="muted small">Data runs from {span.first} to {span.lastAny}.</p>
        </div>
      )}
    </div>
  );
}
