import { useEffect, useMemo, useRef, useState } from "react";
import { postJson, type CombinedSummary, type Filters, type FxQueryResult, type FxRate } from "../api";
import { money } from "../format";
import { useApi } from "../hooks";

/**
 * The scope chip for the current basis. In the Combined view it says whether every month has a saved rate, opens the
 * conversions on click, and adds a warning banner only when a rate is estimated or missing.
 */
export function CombinedNote({ filters }: { filters: Filters }) {
  const params = useMemo(() => new URLSearchParams({ cost_kind: filters.costKind, currency: filters.currency }), [filters.costKind, filters.currency]);
  const { data, error } = useApi<CombinedSummary>(filters.combined ? "/api/fx/combined" : null, filters.combined ? params : null);
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);

  const basis = filters.costKind === "amortized" ? "Amortized" : "Actual";
  if (!filters.combined) return <span className="chip-pill">{basis} · {filters.currency}</span>;
  if (error) return <p className="banner warn scope-banner" role="alert">Could not load the conversion details: {error}</p>;
  if (!data) return <span className="chip-pill">{basis} · Combined {filters.currency}</span>;
  const foreign = data.rows.filter((r) => r.status !== "target");
  const status = data.missing.length ? `${data.missing.length} left out` : data.carried.length ? `${data.carried.length} estimated` : "rates OK";
  const warn = data.missing.length > 0 || data.carried.length > 0;

  /** Looks up today's list-price rate for each currency that is missing one and saves it for each missing month. */
  async function fillMissing() {
    if (!data) return;
    setBusy(true); setFail(null);
    const c = new AbortController();
    try {
      const wanted = new Map<string, string[]>();
      for (const [month, ccy] of data.missing) wanted.set(ccy, [...(wanted.get(ccy) ?? []), month]);
      for (const [ccy, months] of wanted) {
        const r = await postJson<FxQueryResult>("/api/fx/query", { from_currency: ccy, to_currency: data.target }, c.signal);
        for (const month of months) {
          await postJson<FxRate>("/api/fx/rates", {
            month, from_currency: ccy, to_currency: data.target, rate: r.rate, method: "price-list", source: r.source, matched: r.matched,
          }, c.signal);
        }
      }
      window.location.reload();
    } catch (e) {
      setFail(e instanceof Error ? e.message : "Request failed");
      setBusy(false);
    }
  }

  const fillButton = data.missing.length > 0 && (
    <>{" "}<button type="button" disabled={busy} onClick={() => void fillMissing()}>
      {busy ? "Working…" : "Look up today’s rate and save it for these months"}</button>
      <span className="muted small"> Uses today’s list-price rate for every missing month; enter an invoice rate on the Pricing screen instead if you have one.</span></>
  );

  return (
    <>
      <div className="combined" ref={box}>
        <button type="button" className={`chip-pill combined-chip${warn ? " warn" : ""}`} aria-expanded={open} onClick={() => setOpen((o) => !o)}
          title="How other currencies are converted">
          {basis} · Combined {data.target} · {status} ▾
        </button>
        {open && (
          <div className="popover combined-pop" role="dialog" aria-label="Combined view conversions">
            <p><strong>Combined view in {data.target}.</strong>{" "}
              {foreign.length === 0 ? "There is only one currency, so nothing is converted." : "Other currencies are converted at the saved rate for each month."}</p>
            {foreign.length > 0 && (
              <table className="data">
                <thead><tr><th scope="col">Month</th><th scope="col">Currency</th><th scope="col" className="num">Native</th><th scope="col" className="num">Rate</th><th scope="col" className="num">In {data.target}</th></tr></thead>
                <tbody>
                  {foreign.map((r) => (
                    <tr key={`${r.month}-${r.currency}`}>
                      <td>{r.month}</td><td>{r.currency}</td><td className="num">{money(r.native, r.currency)}</td>
                      <td className="num">{r.rate === null ? "no rate" : r.status === "carried" ? `${r.rate} (from ${r.rate_month})` : r.rate}</td>
                      <td className="num">{r.converted === null ? "left out" : money(r.converted, data.target)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="muted small">{data.definition}</p>
          </div>
        )}
      </div>
      {warn && (
        <section className="banner warn scope-banner" aria-label="Combined view conversion">
          {data.carried.length > 0 && (
            <strong>Estimated: {data.carried.map(([m, c, from]) => `${c} ${m} uses the ${from} rate`).join("; ")}. </strong>)}
          {data.missing.length > 0 && (
            <strong>Left out, no rate saved: {data.missing.map(([m, c]) => `${c} ${m}`).join(", ")}.</strong>)}
          {fillButton}
          {fail && <p role="alert">{fail}</p>}
        </section>
      )}
    </>
  );
}
