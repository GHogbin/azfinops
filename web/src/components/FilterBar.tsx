import { DIMS, DIM_LABEL, type CostKind, type Dim, type Filters, type Meta, type Scope } from "../api";
import { dateLabel, lastDayOfMonth } from "../format";
import { labelFor } from "../labels";
import { MultiSelect } from "./MultiSelect";

interface Props {
  meta: Meta;
  filters: Filters;
  onChange: (f: Filters) => void;
}

const KIND_LABEL: Record<CostKind, string> = { actual: "Actual cost", amortized: "Amortized cost" };

function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, (d ?? 1) + n)).toISOString().slice(0, 10);
}

export function FilterBar({ meta, filters, onChange }: Props) {
  const currenciesOf = (kind: CostKind) => [...new Set(meta.scopes.filter((x) => x.cost_kind === kind).map((x) => x.currency))].sort();
  const scope: Scope | undefined = meta.scopes.find((s) => s.cost_kind === filters.costKind && s.currency === filters.currency);
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const setDim = (d: Dim, values: string[]) => {
    const dims = { ...filters.dims };
    if (values.length) dims[d] = values; else delete dims[d];
    set({ dims });
  };
  const badRange = !!filters.start && !!filters.end && filters.start > filters.end;
  const chips = DIMS.flatMap((d) => (filters.dims[d] ?? []).map((v) => ({ d, v })));
  const last = scope?.last_date;

  return (
    <section className="filters" aria-label="Filters">
      <div className="filter-row">
        <label className="field">
          <span>Cost basis and currency</span>
          <select value={`${filters.costKind}|${filters.combined ? `COMBINED:${filters.currency}` : filters.currency}`}
            onChange={(e) => {
              const [k, c] = e.target.value.split("|");
              if (c?.startsWith("COMBINED:")) set({ costKind: k as CostKind, currency: c.slice(9), combined: true });
              else set({ costKind: k as CostKind, currency: c ?? filters.currency, combined: false });
            }}>
            {!scope && <option value={`${filters.costKind}|${filters.currency}`}>{KIND_LABEL[filters.costKind]} · {filters.currency} (no data)</option>}
            {meta.scopes.map((s) => (
              <option key={`${s.cost_kind}|${s.currency}`} value={`${s.cost_kind}|${s.currency}`}>
                {KIND_LABEL[s.cost_kind]} · {s.currency}{s.synthetic ? " · synthetic" : ""}
              </option>
            ))}
            {(["amortized", "actual"] as CostKind[]).flatMap((k) => currenciesOf(k).length > 1
              ? currenciesOf(k).map((c) => (
                <option key={`${k}|COMBINED:${c}`} value={`${k}|COMBINED:${c}`}>{KIND_LABEL[k]} · Combined (all in {c})</option>))
              : [])}
          </select>
        </label>
        <label className="field">
          <span>From</span>
          <input type="date" value={filters.start ?? ""} min={scope?.first_date} max={scope?.last_date}
            onChange={(e) => set({ start: e.target.value || null })} />
        </label>
        <label className="field">
          <span>To</span>
          <input type="date" value={filters.end ?? ""} min={scope?.first_date} max={scope?.last_date}
            onChange={(e) => set({ end: e.target.value || null })} />
        </label>
        <div className="presets" role="group" aria-label="Date presets">
          <button type="button" className="chip-btn" onClick={() => set({ start: null, end: null })}>All data</button>
          {last && <button type="button" className="chip-btn" onClick={() => set({ start: addDays(last, -29), end: last })}>Last 30 days</button>}
          {last && <button type="button" className="chip-btn" onClick={() => set({ start: `${last.slice(0, 7)}-01`, end: lastDayOfMonth(`${last.slice(0, 7)}-01`) })}>Latest month</button>}
        </div>
      </div>
      {badRange && <p className="error small" role="alert">“From” is after “To”; no data will load until this is fixed.</p>}
      {scope && <p className="muted small">Data available {dateLabel(scope.first_date)} to {dateLabel(scope.last_date)}.</p>}
      <div className="filter-row pickers" role="group" aria-label="Dimension filters">
        {DIMS.map((d) => <MultiSelect key={d} dim={d} filters={filters} onChange={(v) => setDim(d, v)} />)}
      </div>
      {chips.length > 0 && (
        <ul className="chips" aria-label="Active filters">
          {chips.map(({ d, v }) => (
            <li key={`${d}:${v}`}>
              <span className="chip">
                <span className="muted">{DIM_LABEL[d]}:</span> {labelFor(v)}
                <button type="button" aria-label={`Remove ${DIM_LABEL[d]} filter ${labelFor(v)}`}
                  onClick={() => setDim(d, (filters.dims[d] ?? []).filter((x) => x !== v))}>×</button>
              </span>
            </li>
          ))}
          <li><button type="button" className="link-btn" onClick={() => set({ dims: {} })}>Clear all</button></li>
        </ul>
      )}
    </section>
  );
}
