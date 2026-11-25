import { useEffect, useMemo, useRef, useState } from "react";
import { filterParams, type Dim, type Filters, type Flow } from "../api";
import { money, moneyShort, num, pct } from "../format";
import { useApi } from "../hooks";
import { bandPath, layoutSankey, OTHER, type SNode } from "../sankey";
import { acctName, enrollmentColor } from "./EnrollmentSplit";

const THIRD: { id: Dim; label: string }[] = [
  { id: "application", label: "Application" },
  { id: "subscription", label: "Subscription" },
  { id: "resource_group", label: "Resource group" },
  { id: "pricing_model", label: "Pricing model" },
];
const LEVEL_LABEL: Partial<Record<Dim, string>> = { billing_account: "Enrollment", service: "Service", application: "Application",
  subscription: "Subscription", resource_group: "Resource group", pricing_model: "Pricing model" };
const NODE_W = 12;
const H = 420;

/** "Where the money goes": enrollment -> service -> a chosen third level, as a Sankey. Loads when scrolled into view. */
export function MoneyFlow({ filters }: { filters: Filters }) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(900);
  const [seen, setSeen] = useState(typeof IntersectionObserver === "undefined");
  const [third, setThird] = useState<Dim>("application");
  const [asTable, setAsTable] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver((e) => {
      const cw = e[0]?.contentRect.width;
      if (cw) setW(Math.max(560, Math.floor(cw)));
    });
    ro?.observe(el);
    const io = seen || typeof IntersectionObserver === "undefined" ? null
      : new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) setSeen(true); }, { rootMargin: "300px" });
    io?.observe(el);
    return () => { ro?.disconnect(); io?.disconnect(); };
  }, [seen]);
  const levels = useMemo<Dim[]>(() => ["billing_account", "service", third], [third]);
  const params = useMemo(() => { const p = filterParams(filters); p.set("levels", levels.join(",")); p.set("n", "8"); return p; }, [filters, levels]);
  const { data, error } = useApi<Flow>(seen ? "/api/cost/flow" : null, seen ? params : null);
  const ccy = filters.currency;

  const paths = useMemo(() => (data?.paths ?? []).map((p) => ({ keys: p.keys, value: num(p.value) })), [data]);
  const lay = useMemo(() => layoutSankey(paths, H - 20, 10), [paths]);

  const name = (level: number, key: string) => {
    if (key === OTHER) return `Other (${data?.grouped[level] ?? 0})`;
    const d = levels[level];
    if (d === "billing_account") return data?.labels.billing_account?.[key] ?? acctName(key);
    return data?.labels[d as string]?.[key] ?? key;
  };
  const groupColor = (g: string) => (g === OTHER ? "var(--muted)" : enrollmentColor(g));
  const left = 180; const right = 200;
  const xs = [left, left + (w - left - right - NODE_W) / 2, w - right - NODE_W];
  const total = num(data?.drawn ?? "0");
  const short = (s: string) => (s.length > 28 ? `${s.slice(0, 27)}…` : s);
  const nodeText = (n: SNode) => `${name(n.level, n.key)}: ${money(n.value, ccy, 2)} (${pct(total ? n.value / total : 0)})`;

  return (
    <section className="card money-flow" aria-label="Where the money goes">
      <header className="card-head">
        <h3>Where the money goes · enrollment → service → {LEVEL_LABEL[third]?.toLowerCase()}</h3>
        <div className="flow-ctl">
          <label className="field">Then by
            <select value={third} onChange={(e) => setThird(e.target.value as Dim)}>
              {THIRD.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
            </select></label>
          <button type="button" className="link-btn" onClick={() => setAsTable((t) => !t)} aria-pressed={asTable}>{asTable ? "Show chart" : "Show as table"}</button>
        </div>
      </header>
      <div ref={box}>
        {error && <p className="error small" role="alert">{error}</p>}
        {!data && !error && <p className="empty" style={{ height: H }}>Loading…</p>}
        {data && !data.available && <p className="muted small">{data.reason}</p>}
        {data?.available && !asTable && (
          <div className="flow-scroll"><svg width={w} height={H} role="img" aria-label={`Sankey of ${money(total, ccy)} from enrollment through service to ${LEVEL_LABEL[third]?.toLowerCase()}. Use Show as table for the values.`}
            onMouseLeave={() => setHover(null)}>
            <g transform="translate(0,10)">
              {lay.links.map((l, i) => {
                const on = hover === null || hover === `${l.from.level}:${l.from.key}` || hover === `${l.to.level}:${l.to.key}` || hover === `g:${l.group}`;
                return (
                  <path key={i} d={bandPath(xs[l.level]! + NODE_W, l.sy, xs[l.level + 1]!, l.ty, Math.max(0.5, l.w))}
                    fill={groupColor(l.group)} fillOpacity={on ? 0.42 : 0.1} onMouseEnter={() => setHover(`g:${l.group}`)}>
                    <title>{`${name(l.from.level, l.from.key)} → ${name(l.to.level, l.to.key)}${l.level > 0 ? ` (${name(0, l.group)})` : ""}: ${money(l.value, ccy, 2)}`}</title>
                  </path>
                );
              })}
              {lay.nodes.map((col, ci) => col.map((n) => {
                const x = xs[ci]!;
                const fill = ci === 0 ? groupColor(n.key) : "var(--text-secondary)";
                const labelLeft = ci === 0;
                return (
                  <g key={`${ci}:${n.key}`} onMouseEnter={() => setHover(ci === 0 ? `g:${n.key}` : `${ci}:${n.key}`)}>
                    <rect x={x} y={n.y} width={NODE_W} height={Math.max(1, n.h)} fill={fill} rx={2}><title>{nodeText(n)}</title></rect>
                    {n.h >= 11 && (
                      <text x={labelLeft ? x - 8 : x + NODE_W + 6} y={n.y + n.h / 2} dy="0.35em" textAnchor={labelLeft ? "end" : "start"}
                        className="flow-label">
                        {short(name(ci, n.key))} <tspan className="flow-value">{moneyShort(n.value, ccy)}</tspan>
                      </text>
                    )}
                  </g>
                );
              }))}
            </g>
          </svg></div>
        )}
        {data?.available && asTable && (
          <div className="table-scroll flow-table">
            <table className="data">
              <thead><tr>{levels.map((d) => <th key={d} scope="col">{LEVEL_LABEL[d]}</th>)}<th scope="col" className="num">Cost</th><th scope="col" className="num">Share</th></tr></thead>
              <tbody>
                {data.paths.map((p, i) => (
                  <tr key={i}>{p.keys.map((k, j) => <td key={j}>{name(j, k ?? "(none)")}</td>)}
                    <td className="num">{money(p.value, ccy, 2)}</td><td className="num">{pct(total ? num(p.value) / total : 0)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {data?.available && (
        <p className="muted small">
          {money(data.drawn, ccy)} drawn{num(data.credits) < 0 ? `; ${money(Math.abs(num(data.credits)), ccy)} of credits and refunds left out, because a Sankey can't draw a negative flow (total ${money(data.total, ccy)})` : ""}.
          {" "}The 8 largest values in each column are shown; the rest are grouped as Other. Hover a band or a box for its value.
        </p>
      )}
    </section>
  );
}
