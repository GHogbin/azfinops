import { useEffect, useMemo, useRef, useState } from "react";
import { DIM_LABEL, filterParams, type Dim, type DimensionValues, type Filters } from "../api";
import { money } from "../format";
import { useApi, useDebounced } from "../hooks";
import { rememberLabel } from "../labels";

interface Props {
  dim: Dim;
  filters: Filters;
  onChange: (values: string[]) => void;
}

/** Value picker for one dimension. The list respects every OTHER active filter (so it never offers values that
 *  would give an empty result) but ignores this dimension's own selection, so you can add to it. */
export function MultiSelect({ dim, filters, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 250);
  const root = useRef<HTMLDivElement>(null);
  const selected = filters.dims[dim] ?? [];

  const params = useMemo(() => {
    const p = filterParams(filters, dim);
    if (q) p.set("search", q);
    p.set("limit", "100");
    return p;
  }, [filters, dim, q]);
  const list = useApi<DimensionValues>(open ? `/api/dimensions/${dim}` : null, open ? params : null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  const toggle = (v: string) => onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);
  const values = list.data?.values ?? [];
  values.forEach((v) => rememberLabel(v.value, v.label));

  return (
    <div className="picker" ref={root}>
      <button type="button" className={`pick-btn${selected.length ? " active" : ""}`} aria-expanded={open}
        aria-haspopup="true" onClick={() => setOpen((o) => !o)}>
        {DIM_LABEL[dim]}{selected.length > 0 && <span className="badge">{selected.length}</span>}
      </button>
      {open && (
        <div className="popover" role="group" aria-label={`${DIM_LABEL[dim]} values`}>
          <input type="search" className="search" placeholder={`Search ${DIM_LABEL[dim].toLowerCase()}…`} value={search}
            onChange={(e) => setSearch(e.target.value)} autoFocus aria-label={`Search ${DIM_LABEL[dim]}`} />
          {list.error && <p className="error small" role="alert">{list.error}</p>}
          <ul className={`options${list.loading ? " loading" : ""}`} aria-busy={list.loading}>
            {values.map((v) => {
              const disabled = v.value === null;
              const name = v.label ?? v.value ?? "(none)";
              return (
                <li key={v.value ?? "(null)"}>
                  <label className={disabled ? "disabled" : ""} title={disabled ? "Rows with no value cannot be selected" : undefined}>
                    <input type="checkbox" disabled={disabled} checked={v.value !== null && selected.includes(v.value)}
                      onChange={() => v.value !== null && toggle(v.value)} />
                    <span className="opt-name">{name}{v.label && v.value && <span className="muted small mono"> {v.value}</span>}</span>
                    <span className="muted small">{money(v.cost, filters.currency)}</span>
                  </label>
                </li>
              );
            })}
            {!list.loading && values.length === 0 && !list.error && <li className="empty small">No matching values.</li>}
          </ul>
          <footer className="pop-foot">
            <span className="muted small">Largest first; top 100 shown{q ? " for this search" : ", search to narrow"}.</span>
            {selected.length > 0 && <button type="button" className="link-btn" onClick={() => onChange([])}>Clear</button>}
          </footer>
        </div>
      )}
    </div>
  );
}
