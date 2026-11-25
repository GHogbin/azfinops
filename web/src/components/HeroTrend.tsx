import { useEffect, useRef, useState } from "react";
import type { AccountSeriesPoint, SeriesPoint } from "../api";
import { dateLabel, money, moneyShort, niceTicks, num } from "../format";
import { acctName, enrollmentColor } from "./EnrollmentSplit";

const H = 150;
const M = { l: 50, r: 6, t: 8, b: 20 };
const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/**
 * The headline's daily trend: the total as a line over a stacked area of each enrollment's contribution, with a
 * zero baseline, value and date axes, weekends shaded, the daily average, and a hover readout per enrollment.
 */
export function HeroTrend({ points, byAccount, currency }: { points: SeriesPoint[]; byAccount?: AccountSeriesPoint[]; currency: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(420);
  const [hv, setHv] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((e) => { const cw = e[0]?.contentRect.width; if (cw) setW(Math.max(240, Math.floor(cw))); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  if (points.length < 2) return null;

  const days = points.map((p) => p.period);
  const total = points.map((p) => num(p.total));
  // Each enrollment's share of each day, largest enrollment at the bottom of the stack.
  const cell = new Map((byAccount ?? []).map((r) => [`${r.period}|${r.account}`, num(r.total)]));
  const sums = new Map<string, number>();
  for (const r of byAccount ?? []) sums.set(r.account, (sums.get(r.account) ?? 0) + num(r.total));
  const accts = [...sums.keys()].sort((a, b) => (sums.get(b) ?? 0) - (sums.get(a) ?? 0));
  const per = accts.map((a) => days.map((d) => cell.get(`${d}|${a}`) ?? 0));
  const stack = accts.length > 1 && per.every((row) => row.every((v) => v >= 0));

  const hi = Math.max(...total, 0);
  const lo = Math.min(...total, 0);
  const ticks = niceTicks(lo, hi || 1, 3);
  const y0 = Math.min(lo, ticks[0] ?? 0); const y1 = Math.max(hi, ticks[ticks.length - 1] ?? hi);
  const pw = Math.max(10, w - M.l - M.r); const ph = H - M.t - M.b;
  const sx = (i: number) => M.l + (i / (days.length - 1)) * pw;
  const sy = (v: number) => M.t + ph - ((v - y0) / Math.max(1e-9, y1 - y0)) * ph;
  const avg = total.reduce((a, v) => a + v, 0) / total.length;

  const layers: { a: string; d: string }[] = [];
  if (stack) {
    const run = days.map(() => 0);
    per.forEach((row, k) => {
      const lower = run.slice();
      row.forEach((v, i) => { run[i] = (run[i] ?? 0) + v; });
      const top = days.map((_d, i) => `${sx(i).toFixed(1)},${sy(run[i] ?? 0).toFixed(1)}`);
      const bot = days.map((_d, i) => `${sx(i).toFixed(1)},${sy(lower[i] ?? 0).toFixed(1)}`).reverse();
      layers.push({ a: accts[k] as string, d: `M${top.join("L")}L${bot.join("L")}Z` });
    });
  }
  const line = total.map((v, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join("");
  const weekend = (d: string) => { const g = new Date(`${d}T00:00:00Z`).getUTCDay(); return g === 0 || g === 6; };
  const band = pw / (days.length - 1);
  const labelIdx = [0, Math.floor((days.length - 1) / 2), days.length - 1];

  return (
    <div ref={box} className="hero-trend">
      <svg width={w} height={H} role="img"
        aria-label={`Daily total cost from ${dateLabel(days[0] as string)} to ${dateLabel(days[days.length - 1] as string)}, between ${moneyShort(Math.min(...total), currency)} and ${moneyShort(Math.max(...total), currency)} a day, average ${moneyShort(avg, currency)}${stack ? ", split by enrollment" : ""}.`}
        onMouseLeave={() => setHv(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const i = Math.round((e.clientX - r.left - M.l) / band);
          setHv(i >= 0 && i < days.length ? i : null);
        }}>
        {days.map((d, i) => (weekend(d) ? <rect key={d} x={sx(i) - band / 2} width={band} y={M.t} height={ph} className="ht-weekend" /> : null))}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.l} x2={w - M.r} y1={sy(t)} y2={sy(t)} className={t === 0 ? "ht-zero" : "ht-grid"} />
            <text x={M.l - 6} y={sy(t)} dy="0.32em" textAnchor="end" className="ht-tick">{moneyShort(t, currency)}</text>
          </g>
        ))}
        {layers.map((l) => <path key={l.a} d={l.d} fill={enrollmentColor(l.a)} fillOpacity={0.55} />)}
        {!stack && <path d={`${line}L${sx(days.length - 1)},${sy(0)}L${sx(0)},${sy(0)}Z`} className="ht-area" />}
        <line x1={M.l} x2={w - M.r} y1={sy(avg)} y2={sy(avg)} className="ht-avg" />
        <path d={line} className="ht-line" />
        <text x={w - M.r} y={sy(avg) + (sy(avg) - M.t > 14 ? -4 : 12)} textAnchor="end" className="ht-tick ht-avg-label">avg {moneyShort(avg, currency)}/day</text>
        {labelIdx.map((i, k) => (
          <text key={k} x={sx(i)} y={H - 4} textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"} className="ht-tick">
            {dateLabel(days[i] as string, false)}</text>
        ))}
        {hv !== null && (
          <>
            <line x1={sx(hv)} x2={sx(hv)} y1={M.t} y2={M.t + ph} className="ht-hover" />
            <circle cx={sx(hv)} cy={sy(total[hv] ?? 0)} r={3.5} className="ht-dot" />
          </>
        )}
      </svg>
      {hv !== null && (
        <div className="tooltip ht-tip" style={{ left: Math.min(Math.max(0, sx(hv) - 110), w - 230) }}>
          <div className="tooltip-label">{DAY[new Date(`${days[hv]}T00:00:00Z`).getUTCDay()]} {dateLabel(days[hv] as string)}</div>
          <div className="tooltip-row"><strong>Total</strong><span><strong>{money(total[hv] ?? 0, currency, 2)}</strong></span></div>
          {accts.length > 1 && accts.map((a, k) => (
            <div key={a} className="tooltip-row"><span className="enr-dot" style={{ background: enrollmentColor(a) }} /> {acctName(a)}
              <span>{money(per[k]?.[hv] ?? 0, currency, 2)}{total[hv] ? ` · ${(((per[k]?.[hv] ?? 0) / (total[hv] as number)) * 100).toFixed(0)}%` : ""}</span></div>
          ))}
        </div>
      )}
      {accts.length > 1 && !stack && <p className="muted small">An enrollment has a negative day (credits), so the line is not split.</p>}
    </div>
  );
}
