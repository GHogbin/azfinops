import type { Filters, PayModels, SeriesPoint } from "../api";
import { money, num, pct } from "../format";
import { acctName, Dot, RatioSplit } from "./EnrollmentSplit";
import { useApi } from "../hooks";

/** Trend line for the headline tile. Shape only: the chart below carries the axes and exact values. */
export function Sparkline({ points, label }: { points: SeriesPoint[]; label: string }) {
  if (points.length < 2) return null;
  const ys = points.map((p) => num(p.total));
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const span = hi - lo || 1;
  const W = 400;
  const H = 70;
  const pad = 6;
  const d = ys.map((y, i) => `${i === 0 ? "M" : "L"}${((i / (ys.length - 1)) * W).toFixed(1)} ${(H - pad - ((y - lo) / span) * (H - 2 * pad)).toFixed(1)}`).join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <path d={d} fill="none" stroke="var(--series-1)" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Pricing models keep one colour each, from palette slots the enrollments do not use. */
const PAY_KNOWN: Record<string, string> = { "On Demand": "var(--cat-3)", OnDemand: "var(--cat-3)", Reservation: "var(--cat-4)", SavingsPlan: "var(--cat-6)", Spot: "var(--cat-8)" };
const PAY_OTHER = ["var(--cat-3)", "var(--cat-4)", "var(--cat-6)", "var(--cat-8)"];
export function payColor(name: string, i: number): string {
  return PAY_KNOWN[name] ?? PAY_OTHER[i % PAY_OTHER.length] as string;
}

/** Usage cost split by pricing model, as one stacked bar. Negative or zero groups are left out of the bar, not hidden from the legend. */
export function PayBar({ filters, data }: { filters: Filters; data: PayModels | null | undefined }) {
  // Comes with the overview response (one query on the server), so the card needs no requests of its own.
  const byAcct = new Map<string, { name: string; cost: number }[]>();
  for (const r of data?.by_account ?? []) {
    byAcct.set(r.account, [...(byAcct.get(r.account) ?? []), { name: r.model ?? "(none)", cost: num(r.cost) }]);
  }
  if (!data) return <p className="muted small">Not available.</p>;
  const rows = data.overall.map((r) => ({ name: r.model ?? "(none)", cost: num(r.cost) }));
  const total = rows.reduce((a, r) => a + Math.max(0, r.cost), 0);
  // Each pricing model keeps the colour it has in the overall bar, so the per-enrollment bars read the same way.
  const colorOf = (name: string) => payColor(name, Math.max(0, rows.findIndex((r) => r.name === name)));
  if (rows.length === 0 || total <= 0) return <p className="muted small">No usage cost in this selection.</p>;
  return (
    <>
      <div className="paybar" role="img" aria-label={`Usage by pricing model: ${rows.map((r) => `${r.name} ${pct(r.cost / total, 0)}`).join(", ")}`}>
        {rows.filter((r) => r.cost > 0).map((r, i) => (
          <div key={r.name} style={{ width: `${(r.cost / total) * 100}%`, background: payColor(r.name, i) }} />
        ))}
      </div>
      <ul className="paylegend">
        {rows.map((r, i) => (
          <li key={r.name}><span aria-hidden="true" style={{ color: payColor(r.name, i) }}>●</span> {r.name} {pct(r.cost / total, 0)}</li>
        ))}
      </ul>
      {byAcct.size > 1 && (
        <ul className="pay-accts" aria-label="Pricing model by enrollment">
          {[...byAcct].map(([a, list]) => {
            const t = list.reduce((x, r) => x + Math.max(0, r.cost), 0);
            const parts = list.filter((r) => r.cost > 0).sort((x, y) => y.cost - x.cost);
            const words = parts.map((r) => `${r.name} ${pct(r.cost / t, 0)}`).join(", ");
            return (
              <li key={a}>
                <span className="muted"><Dot account={a} /> {acctName(a)}</span>
                {t <= 0 ? <span className="muted small">no usage</span> : (
                  <div className="paybar mini" role="img" aria-label={`${acctName(a)}: ${words}`} title={words}>
                    {parts.map((r) => <div key={r.name} style={{ width: `${(r.cost / t) * 100}%`, background: colorOf(r.name) }} />)}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <p className="muted small">Usage charges only, valued as billed on the {filters.costKind} basis.</p>
    </>
  );
}

/** Share of cost tied to an application tag, as a ring. */
export function AttributionRing({ share, gap, currency, accounts }: { share: number | null; gap: string; currency: string; accounts?: { account: string; share: number | null }[] }) {
  const r = 60;
  const c = 2 * Math.PI * r;
  const v = share === null ? 0 : Math.min(1, Math.max(0, share));
  return (
    <div className="ring">
      <svg width="150" height="150" viewBox="0 0 150 150" role="img" aria-label={share === null ? "Attribution not available" : `${pct(share)} of cost tied to an application`}>
        <circle cx="75" cy="75" r={r} fill="none" stroke="var(--grid)" strokeWidth="14" />
        <circle cx="75" cy="75" r={r} fill="none" stroke="var(--series-1)" strokeWidth="14" strokeLinecap="round"
          strokeDasharray={`${(c * v).toFixed(1)} ${c.toFixed(1)}`} transform="rotate(-90 75 75)" />
        <text x="75" y="82" textAnchor="middle" className="ring-text">{pct(share)}</text>
      </svg>
      <div>Cost tied to an application</div>
      <div className="muted small">{money(gap, currency)} untagged or unattributed</div>
      <RatioSplit label="Attribution by enrollment" track={false} items={(accounts ?? []).map((a) => ({ account: a.account, value: a.share }))} />
    </div>
  );
}

const MARK: Record<string, string> = { pass: "✓", warn: "!", fail: "✕", info: "i" };

/** Compact data-quality summary; the full list lives in Data Management. */
export function QualityTile() {
  const { data, error } = useApi<{ checks: { id: string; title: string; status: string }[] }>("/api/data/quality", null);
  if (error) return <p className="muted small">Quality checks unavailable.</p>;
  if (!data) return <p className="muted small" aria-live="polite">Running checks…</p>;
  return (
    <ul className="qlist">
      {data.checks.map((c) => <li key={c.id} className={`q-${c.status}`}><span aria-hidden="true">{MARK[c.status] ?? "i"}</span> {c.title}</li>)}
    </ul>
  );
}

