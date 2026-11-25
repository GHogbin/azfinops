import { type ReactNode, useEffect, useRef, useState } from "react";
import { money, moneyShort, niceTicks } from "../format";
import type { ChartKind } from "../state";

export interface ChartSeries { key: string; name: string; color: string; values: number[] }

interface Props {
  kind: ChartKind;
  x: string[];
  xLabel: (iso: string, short?: boolean) => string;
  series: ChartSeries[];
  currency: string;
  /** Previous period, on the same x positions (null where it has no value). */
  previous?: (number | null)[] | null;
  previousLabel?: string;
  /** Projected running total after the last x. */
  forecast?: { x: string[]; values: number[] } | null;
  /** Budget per x, covering x and the forecast days. */
  budget?: number[] | null;
  /** Clicking a series name in the tooltip or table filters to it. */
  onPick?: (key: string) => void;
  title: string;
}

const H = 360;
const M = { l: 66, r: 14, t: 12, b: 30 };

/** One chart, reshaped by `kind`, in the style of Azure Cost analysis. Hidden series come from the legend. */
export function AnalysisChart(p: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(900);
  const [hv, setHv] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((e) => { const cw = e[0]?.contentRect.width; if (cw) setW(Math.max(320, Math.floor(cw))); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { kind, x, series, currency: ccy } = p;
  const fx = p.forecast?.x ?? [];
  const allX = [...x, ...fx];
  const n = allX.length;
  const totals = x.map((_d, i) => series.reduce((a, s) => a + (s.values[i] ?? 0), 0));

  if (kind === "table") return <TableView {...p} totals={totals} />;
  if (kind === "donut") return <Donut series={series} currency={ccy} title={p.title} onPick={p.onPick} />;

  const pw = Math.max(10, w - M.l - M.r);
  const ph = H - M.t - M.b;
  const band = pw / Math.max(1, n);
  const cx = (i: number) => M.l + band * (i + 0.5);
  // Stacks: positive parts upwards from 0, negative parts downwards (area clips negatives, and says so).
  const up: number[][] = []; const down: number[][] = [];
  let clipped = false;
  series.forEach((s, k) => {
    up[k] = x.map((_d, i) => (k ? up[k - 1]![i]! : 0) + Math.max(0, s.values[i] ?? 0));
    down[k] = x.map((_d, i) => (k ? down[k - 1]![i]! : 0) + Math.min(0, s.values[i] ?? 0));
    if (s.values.some((v) => v < 0)) clipped = true;
  });
  const stacked = kind === "area" || kind === "column";
  const vals: number[] = [0];
  if (stacked) { vals.push(...(up[up.length - 1] ?? []), ...(kind === "column" ? down[down.length - 1] ?? [] : [])); }
  else series.forEach((s) => vals.push(...s.values));
  (p.previous ?? []).forEach((v) => { if (v !== null) vals.push(v); });
  (p.forecast?.values ?? []).forEach((v) => vals.push(v));
  (p.budget ?? []).forEach((v) => vals.push(v));
  const lo = Math.min(...vals); const hi = Math.max(...vals);
  const ticks = niceTicks(lo, hi === lo ? lo + 1 : hi, 5);
  const y0 = Math.min(lo, ticks[0] ?? lo); const y1 = Math.max(hi, ticks[ticks.length - 1] ?? hi);
  const sy = (v: number) => M.t + ph - ((v - y0) / Math.max(1e-9, y1 - y0)) * ph;
  const step = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(pw / 90))));
  /** A polyline through the values, lifting the pen over gaps (null). */
  const lineOf = (vs: (number | null)[]) => {
    let d = ""; let pen = false;
    vs.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${cx(i).toFixed(1)},${sy(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  const body: ReactNode[] = [];
  if (kind === "area") {
    series.forEach((s, k) => {
      const top = x.map((_d, i) => `${cx(i).toFixed(1)},${sy(up[k]![i]!).toFixed(1)}`);
      const bottom = x.map((_d, i) => `${cx(i).toFixed(1)},${sy(k ? up[k - 1]![i]! : 0).toFixed(1)}`).reverse();
      body.push(<path key={s.key} d={`M${top.join("L")}L${bottom.join("L")}Z`} fill={s.color} fillOpacity={0.75} stroke={s.color} strokeWidth={1} />);
    });
  } else if (kind === "column") {
    const bw = Math.max(1, Math.min(40, band * 0.7));
    series.forEach((s, k) => x.forEach((_d, i) => {
      const v = s.values[i] ?? 0;
      if (v === 0) return;
      const a = v > 0 ? (k ? up[k - 1]![i]! : 0) : (k ? down[k - 1]![i]! : 0);
      const b = a + v;
      body.push(<rect key={`${s.key}-${i}`} x={cx(i) - bw / 2} width={bw} y={sy(Math.max(a, b))} height={Math.max(0.5, Math.abs(sy(a) - sy(b)))}
        fill={s.color} opacity={hv === null || hv === i ? 1 : 0.6} />);
    }));
  } else if (kind === "grouped") {
    const gw = Math.min(band * 0.8, 18 * series.length);
    const bw = Math.max(1, gw / Math.max(1, series.length));
    series.forEach((s, k) => x.forEach((_d, i) => {
      const v = s.values[i] ?? 0;
      body.push(<rect key={`${s.key}-${i}`} x={cx(i) - gw / 2 + k * bw} width={Math.max(1, bw - 1)} y={sy(Math.max(0, v))}
        height={Math.max(0.5, Math.abs(sy(0) - sy(v)))} fill={s.color} opacity={hv === null || hv === i ? 1 : 0.6} />);
    }));
  } else {
    series.forEach((s) => body.push(<path key={s.key} d={lineOf(s.values)} fill="none" stroke={s.color} strokeWidth={2} />));
  }

  return (
    <div ref={box} className="ca-chart">
      <svg width={w} height={H} role="img" aria-label={`${p.title}. Use the table view for the values.`}
        onMouseLeave={() => setHv(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const i = Math.floor((e.clientX - r.left - M.l) / band);
          setHv(i >= 0 && i < n ? i : null);
        }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.l} x2={w - M.r} y1={sy(t)} y2={sy(t)} className={t === 0 ? "ca-zero" : "ca-grid"} />
            <text x={M.l - 6} y={sy(t)} dy="0.32em" textAnchor="end" className="ca-tick">{moneyShort(t, ccy)}</text>
          </g>
        ))}
        {allX.map((d, i) => (i % step === 0 ? <text key={d} x={cx(i)} y={H - 10} textAnchor="middle" className="ca-tick">{p.xLabel(d, true)}</text> : null))}
        {body}
        {p.previous && <path d={lineOf(p.previous)} fill="none" className="ca-prev" />}
        {p.forecast && x.length > 0 && (
          <path d={`M${cx(x.length - 1)},${sy(totals[totals.length - 1] ?? 0)}${p.forecast.values.map((v, i) => `L${cx(x.length + i)},${sy(v)}`).join("")}`}
            fill="none" className="ca-forecast" />
        )}
        {p.budget && <path d={lineOf(p.budget)} fill="none" className="ca-budget" />}
        {hv !== null && <line x1={cx(hv)} x2={cx(hv)} y1={M.t} y2={M.t + ph} className="ca-hover" />}
      </svg>
      {hv !== null && (
        <div className="tooltip ca-tip" style={{ left: Math.min(cx(hv) + 12, w - 250) }}>
          <div className="tooltip-label">{p.xLabel(allX[hv] ?? "")}{hv >= x.length ? " · forecast" : ""}</div>
          {hv < x.length && series.map((s) => (
            <div key={s.key} className="tooltip-row"><span className="enr-dot" style={{ background: s.color }} /> {s.name}
              <span>{money(s.values[hv] ?? 0, ccy, 2)}</span></div>
          ))}
          {hv < x.length && <div className="tooltip-row ca-tip-total">Total<span>{money(totals[hv] ?? 0, ccy, 2)}</span></div>}
          {hv >= x.length && p.forecast && <div className="tooltip-row">Forecast<span>{money(p.forecast.values[hv - x.length] ?? 0, ccy, 2)}</span></div>}
          {p.previous && p.previous[hv] !== null && p.previous[hv] !== undefined && (
            <div className="tooltip-row">{p.previousLabel ?? "Previous period"}<span>{money(p.previous[hv] as number, ccy, 2)}</span></div>)}
          {p.budget && p.budget[hv] !== undefined && <div className="tooltip-row">Budget<span>{money(p.budget[hv] as number, ccy, 2)}</span></div>}
        </div>
      )}
      {kind === "area" && clipped && <p className="muted small">Negative amounts (credits) are not drawn in a stacked area; use stacked column or the table to see them.</p>}
    </div>
  );
}

function Donut({ series, currency, title, onPick }: { series: ChartSeries[]; currency: string; title: string; onPick?: (k: string) => void }) {
  const parts = series.map((s) => ({ ...s, v: s.values.reduce((a, b) => a + b, 0) })).filter((s) => s.v > 0);
  const total = parts.reduce((a, s) => a + s.v, 0);
  const R = 120; const r = 74; const c = 140;
  let a0 = -Math.PI / 2;
  const arc = (a: number, b: number) => {
    const large = b - a > Math.PI ? 1 : 0;
    const p = (ang: number, rad: number) => `${c + rad * Math.cos(ang)},${c + rad * Math.sin(ang)}`;
    return `M${p(a, R)}A${R},${R} 0 ${large} 1 ${p(b, R)}L${p(b, r)}A${r},${r} 0 ${large} 0 ${p(a, r)}Z`;
  };
  return (
    <div className="ca-donut">
      <svg width={280} height={280} role="img" aria-label={`${title}: ${parts.map((s) => `${s.name} ${money(s.v, currency)}`).join(", ")}`}>
        {parts.map((s) => {
          const a1 = a0 + (s.v / total) * Math.PI * 2 * 0.9999;
          const d = arc(a0, a1);
          a0 = a1;
          return <path key={s.key} d={d} fill={s.color}><title>{`${s.name}: ${money(s.v, currency, 2)} (${((s.v / total) * 100).toFixed(1)}%)`}</title></path>;
        })}
        <text x={c} y={c - 6} textAnchor="middle" className="ca-donut-total">{moneyShort(total, currency)}</text>
        <text x={c} y={c + 14} textAnchor="middle" className="ca-tick">total</text>
      </svg>
      <ul className="ca-donut-list">
        {parts.map((s) => (
          <li key={s.key}><span className="enr-dot" style={{ background: s.color }} />
            {onPick && s.key !== "__other__" ? <button type="button" className="link-btn" onClick={() => onPick(s.key)}>{s.name}</button> : <span>{s.name}</span>}
            <span>{money(s.v, currency)}</span><span className="muted">{((s.v / total) * 100).toFixed(1)}%</span></li>
        ))}
      </ul>
      {series.some((s) => s.values.reduce((a, b) => a + b, 0) <= 0) && <p className="muted small">Series that net to zero or less (credits) are not drawn in a donut.</p>}
    </div>
  );
}

function TableView(p: Props & { totals: number[] }) {
  const ccy = p.currency;
  return (
    <div className="table-scroll ca-table">
      <table className="data">
        <thead><tr><th scope="col">Period</th>
          {p.series.map((s) => <th key={s.key} scope="col" className="num">
            {p.onPick && s.key !== "__other__" ? <button type="button" className="link-btn" onClick={() => p.onPick!(s.key)}>{s.name}</button> : s.name}</th>)}
          <th scope="col" className="num">Total</th>
          {p.previous && <th scope="col" className="num">{p.previousLabel ?? "Previous"}</th>}
          {p.budget && <th scope="col" className="num">Budget</th>}
        </tr></thead>
        <tbody>
          {p.x.map((d, i) => (
            <tr key={d}><th scope="row">{p.xLabel(d)}</th>
              {p.series.map((s) => <td key={s.key} className="num">{money(s.values[i] ?? 0, ccy, 2)}</td>)}
              <td className="num"><strong>{money(p.totals[i] ?? 0, ccy, 2)}</strong></td>
              {p.previous && <td className="num">{p.previous[i] === null || p.previous[i] === undefined ? "—" : money(p.previous[i] as number, ccy, 2)}</td>}
              {p.budget && <td className="num">{p.budget[i] === undefined ? "—" : money(p.budget[i] as number, ccy, 2)}</td>}
            </tr>
          ))}
          {p.forecast?.x.map((d, i) => (
            <tr key={d} className="muted"><th scope="row">{p.xLabel(d)} (forecast)</th>
              {p.series.map((s) => <td key={s.key} />)}
              <td className="num">{money(p.forecast!.values[i] ?? 0, ccy, 2)}</td>
              {p.previous && <td />}
              {p.budget && <td className="num">{p.budget[p.x.length + i] === undefined ? "—" : money(p.budget[p.x.length + i] as number, ccy, 2)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
