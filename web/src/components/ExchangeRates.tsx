import { useRef, useState } from "react";
import { postJson, type FxQueryResult, type FxRate } from "../api";
import { useApi } from "../hooks";
import { ErrorBox } from "./Status";

const CCY = /^[A-Z]{3}$/;
const thisMonth = () => new Date().toISOString().slice(0, 7);

/** Looks up the Azure list-price exchange rate and stores rates by month. Rates are only recorded here; no cost
 *  figure elsewhere in the dashboard is converted by them. */
export function ExchangeRates() {
  const saved = useApi<{ rates: FxRate[] }>("/api/fx/rates", null);
  const [from, setFrom] = useState("USD");
  const [to, setTo] = useState("GBP");
  const [month, setMonth] = useState(thisMonth());
  const [manual, setManual] = useState("");
  const [result, setResult] = useState<FxQueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const ctl = useRef<AbortController | null>(null);

  const valid = CCY.test(from) && CCY.test(to) && from !== to;

  async function query() {
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    setBusy(true); setError(null); setNote(null); setResult(null);
    try {
      setResult(await postJson<FxQueryResult>("/api/fx/query", { from_currency: from, to_currency: to }, c.signal));
    } catch (e) {
      if (!c.signal.aborted) setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      if (!c.signal.aborted) setBusy(false);
    }
  }

  /** One step: look up today's rate and store it for the chosen month. */
  async function updateNow() {
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    setBusy(true); setError(null); setNote(null); setResult(null);
    try {
      const r = await postJson<FxQueryResult>("/api/fx/query", { from_currency: from, to_currency: to }, c.signal);
      await postJson<FxRate>("/api/fx/rates", {
        month, from_currency: from, to_currency: to, rate: r.rate, method: "price-list", source: r.source, matched: r.matched,
      }, c.signal);
      setResult(r);
      setNote(`Updated: 1 ${from} = ${r.rate} ${to}, saved for ${month}.`);
      saved.reload();
    } catch (e) {
      if (!c.signal.aborted) setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      if (!c.signal.aborted) setBusy(false);
    }
  }

  async function save(rate: string, method: "price-list" | "manual", source: string, matched: number | null) {
    const c = new AbortController();
    setBusy(true); setError(null); setNote(null);
    try {
      await postJson<FxRate>("/api/fx/rates", { month, from_currency: from, to_currency: to, rate, method, source, matched }, c.signal);
      setNote(`Saved ${from}→${to} ${rate} for ${month}.`);
      setManual("");
      saved.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-label="Exchange rates">
      <header className="card-head"><h3>Exchange rates</h3></header>
      <p className="muted">
        Looks up the rate on the public Azure price list, the same list the Azure Pricing Calculator uses, by comparing the
        price of the same items in two currencies. It is today’s list-price rate, not an invoice rate. This is the only
        screen that contacts the internet, and only when you press Query. Saved rates are used by the Combined option in Filters,
        which converts every other currency into one, month by month.
      </p>
      <form className="fx-form" onSubmit={(e) => { e.preventDefault(); void query(); }}>
        <label>From<input value={from} maxLength={3} onChange={(e) => setFrom(e.target.value.toUpperCase())} aria-invalid={!CCY.test(from)} /></label>
        <label>To<input value={to} maxLength={3} onChange={(e) => setTo(e.target.value.toUpperCase())} aria-invalid={!CCY.test(to)} /></label>
        <label>Month<input type="month" value={month} onChange={(e) => setMonth(e.target.value)} /></label>
        <button type="submit" disabled={!valid || busy}>{busy ? "Working…" : "Query rate"}</button>
        <button type="button" disabled={!valid || busy || !month} onClick={() => void updateNow()}>Update {month} now</button>
      </form>
      {error && <p className="banner warn" role="alert">{error}</p>}
      {note && <p className="banner ok" role="status">{note}</p>}
      {result && (
        <div className="fx-result" aria-live="polite">
          <p><strong>1 {result.from} = {result.rate} {result.to}</strong>
            <span className="muted"> · {result.matched} items matched · range {result.min} to {result.max} · {result.queried_at}</span></p>
          <p className="muted small">{result.definition}</p>
          <button type="button" disabled={busy || !month}
            onClick={() => void save(result.rate, "price-list", result.source, result.matched)}>Save for {month}</button>
        </div>
      )}
      <form className="fx-form" onSubmit={(e) => { e.preventDefault(); if (valid && manual) void save(manual.trim(), "manual", "entered by hand", null); }}>
        <label>Or enter a rate by hand (e.g. from an invoice)
          <input inputMode="decimal" value={manual} onChange={(e) => setManual(e.target.value)} placeholder="0.79" /></label>
        <button type="submit" disabled={!valid || busy || !manual.trim() || !month}>Save for {month}</button>
      </form>
      {saved.error && <ErrorBox message={saved.error} onRetry={saved.reload} />}
      {saved.data && (saved.data.rates.length === 0
        ? <p className="muted">No rates saved yet.</p>
        : (
          <table className="data">
            <caption className="sr-only">Saved exchange rates</caption>
            <thead><tr><th scope="col">Month</th><th scope="col">Pair</th><th scope="col">Rate</th><th scope="col">How</th><th scope="col">Saved</th></tr></thead>
            <tbody>
              {saved.data.rates.map((r) => (
                <tr key={`${r.month}-${r.from}-${r.to}`}>
                  <td>{r.month}</td><td>{r.from}→{r.to}</td><td className="num">{r.rate}</td>
                  <td>{r.method === "manual" ? "Entered by hand" : `Price list (${r.matched ?? "?"} items)`}</td>
                  <td>{r.saved_at ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
    </section>
  );
}
