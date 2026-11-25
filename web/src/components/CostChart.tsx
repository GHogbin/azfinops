import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { AccountSeriesPoint, SeriesPoint } from "../api";
import { acctName, Dot, enrollmentColor } from "./EnrollmentSplit";
import { compact, dateLabel, dayMs, money, monthLabel, niceTicks, num } from "../format";

interface Props {
  points: SeriesPoint[];
  currency: string;
  grain: "day" | "month";
  /** Which figure to plot. */
  measure: "total" | "usage";
  title: string;
  /** The same series per enrollment; with `stacked`, the chart stacks the enrollments. */
  byAccount?: AccountSeriesPoint[];
  stacked?: boolean;
}

const H = 280;
const M = { l: 56, r: 12, t: 12, b: 26 };

/** One series, one axis. Daily = line+area (continuous), monthly = bars (discrete). Crosshair/tooltip on hover
 *  and with arrow keys; a table view carries the same numbers for screen readers and copy/paste. */
export function CostChart({ points, currency, grain, measure, title, byAccount, stacked }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect.width;
      if (cw) setW(Math.max(280, Math.floor(cw)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const label = (iso: string, short = false) => (grain === "month" ? monthLabel(iso) : dateLabel(iso, !short));
  const values = points.map((p) => num(p[measure]));
  // Per-enrollment layers. Stacking needs every part to be zero or more; a credit-heavy day falls back to one series.
  const accts = stacked && byAccount ? [...new Set(byAccount.map((r) => r.account))].sort() : [];
  const cell = new Map((byAccount ?? []).map((r) => [`${r.period}|${r.account}`, num(r[measure])]));
  const per = accts.map((a) => points.map((p) => cell.get(`${p.period}|${a}`) ?? 0));
  const canStack = accts.length > 1 && per.every((row) => row.every((v) => v >= 0));
  const stackNote = stacked && accts.length > 1 && !canStack ? "Some days have negative amounts per enrollment, so the enrollments are not stacked." : null;

  let body: JSX.Element;
  if (points.length === 0) {
    body = <p className="empty">No cost in this selection.</p>;
  } else if (asTable) {
    body = (
      <div className="table-scroll" style={{ maxHeight: H + 40 }}>
        <table className="data">
          <caption className="sr-only">{title}</caption>
          <thead><tr><th scope="col">{grain === "month" ? "Month" : "Date"}</th>
            {canStack && accts.map((a) => <th scope="col" className="num" key={a}>{acctName(a)}</th>)}
            <th scope="col" className="num">Cost ({currency})</th></tr></thead>
          <tbody>
            {points.map((p, i) => (
              <tr key={p.period}><th scope="row">{label(p.period)}</th>
                {canStack && accts.map((a, k) => <td className="num" key={a}>{money(per[k]?.[i] ?? 0, currency, 2)}</td>)}
                <td className="num">{money(values[i] ?? 0, currency, 2)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  } else {
    const iw = w - M.l - M.r;
    const ih = H - M.t - M.b;
    const xs = points.map((p) => dayMs(p.period));
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);
    const lo = Math.min(0, ...values);
    const hi = Math.max(0, ...values);
    const ticks = niceTicks(lo, hi * 1.04 || 1, 5);
    const top = Math.max(hi, ...ticks) || 1;
    const span = top - lo || 1;
    const bandW = iw / points.length;
    // bars sit in equal bands; lines use real time so gaps in the data show as gaps
    const sx = (i: number): number => {
      if (grain === "month") return M.l + bandW * (i + 0.5);
      return points.length === 1 ? M.l + iw / 2 : M.l + (((xs[i] ?? x0) - x0) / (x1 - x0)) * iw;
    };
    const sy = (v: number): number => M.t + ih - ((v - lo) / span) * ih;
    const base = sy(0);
    const line = values.map((v, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join("");
    const area = `${line}L${sx(points.length - 1).toFixed(1)},${base.toFixed(1)}L${sx(0).toFixed(1)},${base.toFixed(1)}Z`;
    const nLabels = Math.min(points.length, Math.max(2, Math.floor(iw / 90)));
    const labelIdx = new Set(Array.from({ length: nLabels }, (_, k) => Math.round((k * (points.length - 1)) / Math.max(1, nLabels - 1))));

    const nearest = (px: number): number => {
      let best = 0;
      let bd = Infinity;
      for (let i = 0; i < points.length; i++) {
        const d = Math.abs(sx(i) - px);
        if (d < bd) { bd = d; best = i; }
      }
      return best;
    };
    const onMove = (e: PointerEvent<SVGSVGElement>) => {
      const r = e.currentTarget.getBoundingClientRect();
      setHover(nearest(e.clientX - r.left));
    };
    const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
      if (e.key === "ArrowRight") { e.preventDefault(); setHover((h) => Math.min(points.length - 1, (h ?? -1) + 1)); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); setHover((h) => Math.max(0, (h ?? points.length) - 1)); }
      else if (e.key === "Escape") setHover(null);
    };
    const hv = hover !== null && hover < points.length ? hover : null;
    const hx = hv === null ? 0 : sx(hv);
    const hy = hv === null ? 0 : sy(values[hv] ?? 0);
    const sum = values.reduce((a, b) => a + b, 0);
    const first = points[0]?.period ?? "";
    const last = points[points.length - 1]?.period ?? "";

    body = (
      <div className="chart-wrap">
        <svg
          width={w} height={H} viewBox={`0 0 ${w} ${H}`} tabIndex={0} role="img"
          aria-label={`${title}. ${points.length} ${grain === "month" ? "months" : "days"} from ${label(first)} to ${label(last)}, total ${money(sum, currency)}. Use the table view for exact values.`}
          onPointerMove={onMove} onPointerLeave={() => setHover(null)} onKeyDown={onKey} onBlur={() => setHover(null)}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={M.l} x2={w - M.r} y1={sy(t)} y2={sy(t)} />
              <text className="tick" x={M.l - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">{compact(t)}</text>
            </g>
          ))}
          <line className="axis" x1={M.l} x2={w - M.r} y1={base} y2={base} />
          {canStack ? (() => {
            // cumulative bottoms and tops per enrollment, in a fixed order so each keeps its place in the stack
            const lo0: number[][] = []; const hi0: number[][] = [];
            const run = points.map(() => 0);
            per.forEach((row, k) => {
              lo0[k] = run.slice();
              row.forEach((v, i) => { run[i] = (run[i] ?? 0) + v; });
              hi0[k] = run.slice();
            });
            if (grain === "month") {
              const bw = Math.min(56, bandW * 0.6);
              return accts.map((a, k) => points.map((p, i) => {
                const y = sy(hi0[k]?.[i] ?? 0); const h = sy(lo0[k]?.[i] ?? 0) - y;
                return h > 0 ? <rect key={`${a}-${p.period}`} x={sx(i) - bw / 2} y={y} width={bw} height={h}
                  fill={enrollmentColor(a)} opacity={hv === null || hv === i ? 1 : 0.55} /> : null;
              }));
            }
            return accts.map((a, k) => {
              const up = points.map((_, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(hi0[k]?.[i] ?? 0).toFixed(1)}`).join("");
              const down = points.map((_, i) => points.length - 1 - i).map((i) => `L${sx(i).toFixed(1)},${sy(lo0[k]?.[i] ?? 0).toFixed(1)}`).join("");
              return <path key={a} d={`${up}${down}Z`} fill={enrollmentColor(a)} fillOpacity={0.8} stroke={enrollmentColor(a)} strokeWidth={1} />;
            });
          })() : grain === "month" ? (
            values.map((v, i) => {
              const bw = Math.min(56, bandW * 0.6);
              const y = Math.min(sy(v), base);
              return <rect key={points[i]?.period} className="bar" x={sx(i) - bw / 2} y={y} width={bw} height={Math.max(1, Math.abs(base - sy(v)))} rx={4} opacity={hv === null || hv === i ? 1 : 0.55} />;
            })
          ) : (
            <>
              <path className="area" d={area} />
              <path className="line" d={line} />
            </>
          )}
          {points.map((p, i) => labelIdx.has(i) && (
            <text key={p.period} className="tick" x={sx(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}>{label(p.period, true)}</text>
          ))}
          {hv !== null && grain === "day" && (
            <g pointerEvents="none">
              <line className="crosshair" x1={hx} x2={hx} y1={M.t} y2={base} />
              <circle className="marker" cx={hx} cy={hy} r={5} />
            </g>
          )}
        </svg>
        {hv !== null && (
          <div className="tooltip" style={{ left: Math.min(w - 170, Math.max(M.l, hx + 14)), top: M.t + 4 }} aria-hidden="true">
            <div className="tooltip-title">{label(points[hv]?.period ?? "")}</div>
            <div className="tooltip-value">{money(values[hv] ?? 0, currency, 2)}</div>
            {canStack && accts.map((a, k) => (
              <div key={a} className="tooltip-row"><Dot account={a} /> {acctName(a)} <span>{money(per[k]?.[hv] ?? 0, currency, 2)}</span></div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <section className="card" aria-label={title}>
      <header className="card-head">
        <h3>{title}</h3>
        {canStack && !asTable && (
          <ul className="chart-legend" aria-label="Enrollments">
            {accts.map((a) => <li key={a}><Dot account={a} /> {acctName(a)}</li>)}
          </ul>
        )}
        <button type="button" className="link-btn" onClick={() => setAsTable((t) => !t)} aria-pressed={asTable}>
          {asTable ? "Show chart" : "Show table"}
        </button>
      </header>
      <div ref={box}>{body}</div>
      {stackNote && <p className="muted small">{stackNote}</p>}
    </section>
  );
}
