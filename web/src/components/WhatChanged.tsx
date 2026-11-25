import { useMemo, useState } from "react";
import { filterParams, type Dim, type Filters, type MoverRow, type Movers } from "../api";
import { moneyShort, monthLabel, num, pct } from "../format";
import { useApi } from "../hooks";
import { rememberLabel } from "../labels";

const DIMS: { id: Dim; label: string }[] = [
  { id: "service", label: "Service" },
  { id: "subscription", label: "Subscription" },
];

interface Step { label: string; kind: "total" | "up" | "down" | "other"; start: number; end: number; key?: string | null }

/** Steps of the bridge: the earlier total, the increases, the rest, the decreases, the later total. */
export function bridgeSteps(m: Movers, name: (r: MoverRow) => string): Step[] {
  const t0 = num(m.total_from); const t1 = num(m.total_to);
  const steps: Step[] = [{ label: monthLabel(m.month_from ?? ""), kind: "total", start: 0, end: t0 }];
  let run = t0;
  const add = (label: string, kind: Step["kind"], v: number, key?: string | null) => { steps.push({ label, kind, start: run, end: run + v, key }); run += v; };
  for (const r of m.increases) add(name(r), "up", num(r.change), r.key);
  const shown = [...m.increases, ...m.decreases].reduce((a, r) => a + num(r.change), 0);
  const rest = num(m.change) - shown;
  if (Math.abs(rest) > 0.005) add("Everything else", "other", rest);
  for (const r of m.decreases) add(name(r), "down", num(r.change), r.key);
  steps.push({ label: monthLabel(m.month_to ?? ""), kind: "total", start: 0, end: t1 });
  return steps;
}

/** A horizontal bridge (waterfall). The axis starts near the lowest running total, not zero, so changes are visible. */
function Bridge({ data, ccy, name, onPick }: { data: Movers; ccy: string; name: (r: MoverRow) => string; onPick: (key: string) => void }) {
  const steps = bridgeSteps(data, name);
  const vals = steps.flatMap((s) => (s.kind === "total" ? [s.end] : [s.start, s.end]));
  const lo = Math.min(...vals); const hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.25, Math.abs(hi) * 0.002, 1);
  const axis0 = lo - pad > 0 ? lo - pad : Math.min(0, lo);
  const span = Math.max(1e-9, hi + pad * 0.2 - axis0);
  const at = (v: number) => `${((v - axis0) / span) * 100}%`;
  return (
    <div className="bridge" role="table" aria-label="Bridge from one month's total to the next">
      {steps.map((s, i) => {
        const a = s.kind === "total" ? axis0 : Math.min(s.start, s.end);
        const b = s.kind === "total" ? s.end : Math.max(s.start, s.end);
        const v = s.end - s.start;
        return (
          <div key={i} className={`bridge-row ${s.kind}`} role="row">
            <span role="cell" className="bridge-name">
              {s.key ? <button type="button" className="link-btn" onClick={() => onPick(s.key as string)} title={`Filter to ${s.label}`}>{s.label}</button> : s.label}
            </span>
            <span role="cell" className="bridge-track" aria-hidden="true">
              <span className="bridge-bar" style={{ left: at(a), width: `calc(${at(b)} - ${at(a)})` }} />
            </span>
            <span role="cell" className={`bridge-val ${s.kind === "up" ? "mv-up" : s.kind === "down" ? "mv-down" : ""}`}>
              {s.kind === "total" ? moneyShort(s.end, ccy) : `${v >= 0 ? "▲" : "▼"} ${moneyShort(Math.abs(v), ccy)}`}
            </span>
          </div>
        );
      })}
      {axis0 > 0 && <p className="muted small">The axis starts at {moneyShort(axis0, ccy)}, not zero, so the changes are visible; the totals are not to scale with zero.</p>}
    </div>
  );
}

/** The biggest increases and decreases between two whole months. Clicking a row filters the workspace to it. */
export function WhatChanged({ filters, onPick }: { filters: Filters; onPick: (dim: Dim, value: string) => void }) {
  const [dim, setDim] = useState<Dim>("service");
  const [view, setView] = useState<"bridge" | "list">("bridge");
  const params = useMemo(() => { const p = filterParams(filters); p.set("dimension", dim); p.set("n", "5"); return p; }, [filters, dim]);
  const { data, error } = useApi<Movers>("/api/cost/movers", params);
  const ccy = filters.currency;

  const row = (r: MoverRow) => {
    rememberLabel(r.key, r.label);
    const name = r.label ?? r.key ?? "(none)";
    const up = num(r.change) > 0;
    return (
      <li key={r.key ?? "(null)"}>
        {r.key !== null
          ? <button type="button" className="link-btn" onClick={() => onPick(dim, r.key as string)} title={`Filter to ${name}`}>{name}</button>
          : <span>{name}</span>}
        <span className={up ? "mv-up" : "mv-down"}>{up ? "▲" : "▼"} {moneyShort(Math.abs(num(r.change)), ccy)}</span>
        <span className="muted small">{r.share === null ? "new" : pct(Math.abs(r.share), 0)}</span>
      </li>
    );
  };

  return (
    <section className="card tile-changed" aria-label="What changed">
      <header className="card-head">
        <h3>What changed{data?.available && data.month_from && data.month_to ? ` · ${monthLabel(data.month_from)} → ${monthLabel(data.month_to)}` : ""}</h3>
        <div className="mv-ctl">
          <div className="seg" role="group" aria-label="Group changes by">
            {DIMS.map((d) => <button key={d.id} type="button" aria-pressed={dim === d.id} onClick={() => setDim(d.id)}>{d.label}</button>)}
          </div>
          <div className="seg" role="group" aria-label="Show as">
            <button type="button" aria-pressed={view === "bridge"} onClick={() => setView("bridge")}>Bridge</button>
            <button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>List</button>
          </div>
        </div>
      </header>
      {error && <p className="muted small">Could not load the changes: {error}</p>}
      {!data && !error && <p className="muted small" aria-live="polite">Loading…</p>}
      {data && !data.available && <p className="muted small">{data.reason}</p>}
      {data?.available && (
        <>
          {view === "bridge" && <Bridge data={data} ccy={ccy} name={(r) => { rememberLabel(r.key, r.label); return r.label ?? r.key ?? "(none)"; }}
            onPick={(k) => onPick(dim, k)} />}
          {view === "list" && <div className="movers">
            <div><h4 className="small muted">Biggest increases</h4>
              {data.increases.length ? <ol className="mv-list">{data.increases.map(row)}</ol> : <p className="muted small">None.</p>}</div>
            <div><h4 className="small muted">Biggest decreases</h4>
              {data.decreases.length ? <ol className="mv-list">{data.decreases.map(row)}</ol> : <p className="muted small">None.</p>}</div>
          </div>}
          <p className="muted small">
            Total {num(data.change) >= 0 ? "up" : "down"} {moneyShort(Math.abs(num(data.change)), ccy)}, from {moneyShort(data.total_from ?? "0", ccy)} to {moneyShort(data.total_to ?? "0", ccy)}.
            {(data.complete_from === false || data.complete_to === false) && " One of these months is incomplete, so part of the change may be missing days."}
          </p>
        </>
      )}
    </section>
  );
}
