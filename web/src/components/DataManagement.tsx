import { useEffect, useRef, useState } from "react";
import {
  ApiError, postJson, type CheckStatus, type ImportBatch, type Imports, type DataQuality, type SchemaListing, type SqlResult,
} from "../api";
import { toCsv } from "../csv";
import { count } from "../format";
import { useApi } from "../hooks";
import { ImportPanel } from "./ImportPanel";
import { ErrorBox, TimingNote } from "./Status";

const STATUS_LABEL: Record<CheckStatus, string> = { pass: "Pass", warn: "Check", fail: "Fail", info: "Note" };
const STATUS_MARK: Record<CheckStatus, string> = { pass: "✓", warn: "!", fail: "✕", info: "i" };

const EXAMPLES: [string, string][] = [
  ["Cost by month", "SELECT usage_month, cost_kind, billing_currency, sum(cost) AS cost, sum(row_count) AS rows\nFROM agg_daily\nGROUP BY ALL\nORDER BY 1, 2"],
  ["Top services", "SELECT meter_category, sum(cost) AS cost\nFROM agg_daily\nWHERE cost_kind = 'amortized'\nGROUP BY 1\nORDER BY 2 DESC\nLIMIT 20"],
  ["Imported batches", "SELECT batch_id, status, cost_kind, period_start, period_end, row_count, rejected_row_count\nFROM import_batches\nORDER BY batch_id DESC"],
];

function when(ts: string | null): string {
  return ts ? ts.replace("T", " ").slice(0, 16) : "—";
}

function bytes(n: number | null): string {
  if (n === null) return "—";
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${count(Math.round(n / 1e3))} KB`;
}

function QualityChecks() {
  const [full, setFull] = useState(false);
  const params = full ? new URLSearchParams({ full: "true" }) : null;
  const { data, error, loading, reload, timing } = useApi<DataQuality>("/api/data/quality", params);
  if (error) return (
    <section className="card">
      <ErrorBox message={error} onRetry={reload} />
      {full && <button type="button" onClick={() => setFull(false)}>Return to fast integrity checks</button>}
    </section>
  );
  if (!data) return <p className="empty" aria-live="polite">Running checks…</p>;
  const s = data.summary;
  return (
    <section className="card" aria-label="Data quality checks" aria-busy={loading}>
      <header className="card-head"><h3>Data quality</h3>
        <span className="muted">{s.fail} failed · {s.warn} to check · {s.pass} passed · {s.info} notes</span></header>
      <p className="muted small">
        {data.full_reconciliation
          ? "Full reconciliation compares imported facts and both aggregate tables. The normal query deadline still applies."
          : "Fast checks use import metadata and aggregates. Raw fact-to-aggregate and resource-total reconciliation has not been run."}
      </p>
      <ul className="checks">
        {data.checks.map((c) => (
          <li key={c.id} className={`check s-${c.status}`}>
            <span className="check-mark" aria-hidden="true">{STATUS_MARK[c.status]}</span>
            <div><strong>{c.title}</strong> <span className="check-tag">{STATUS_LABEL[c.status]}</span>
              <div className="muted small">{c.detail}</div></div>
          </li>
        ))}
      </ul>
      <button type="button" className="link-btn" disabled={loading} onClick={() => { setFull(false); if (!full) reload(); }}>
        Run fast checks
      </button>{" "}
      <button type="button" disabled={loading} onClick={() => { setFull(true); if (full) reload(); }}>
        Run full reconciliation
      </button>
      <TimingNote timing={timing} loading={loading} />
    </section>
  );
}

function BatchRow({ b }: { b: ImportBatch }) {
  const period = b.period_start ? `${b.period_start} → ${b.period_end ?? "…"}` : "—";
  return (
    <details className="batch">
      <summary>
        <span className={`pill ${b.status}`}>{b.status}</span>{" "}
        <strong>#{b.batch_id}</strong> · {b.cost_kind} · {period} · {b.rows === null ? "—" : count(b.rows)} accepted source rows
        {b.rejected ? <span className="error"> · {count(b.rejected)} rejected</span> : null}
        {b.synthetic ? " · synthetic" : ""}
        <span className="muted small"> · {when(b.created_at)}</span>
      </summary>
      <div className="batch-body">
        {b.notes && <p className="muted small">{b.notes}</p>}
        <table className="data">
          <caption className="sr-only">Files in batch {b.batch_id}</caption>
          <thead><tr><th scope="col">File</th><th scope="col" className="num">Rows read</th><th scope="col" className="num">Size</th>
            <th scope="col">Checksum</th><th scope="col">Format</th></tr></thead>
          <tbody>
            {b.files.map((f) => (
              <tr key={f.sha256 + f.file_name}><td>{f.file_name}</td><td className="num">{f.rows === null ? "—" : count(f.rows)}</td>
                <td className="num">{bytes(f.bytes)}</td><td className="mono">{f.sha256}</td><td>{f.schema_id ?? "—"}</td></tr>
            ))}
          </tbody>
        </table>
        {b.validation.length === 0
          ? <p className="muted small">No validation messages.</p>
          : (
            <table className="data">
              <caption className="sr-only">Validation messages for batch {b.batch_id}</caption>
              <thead><tr><th scope="col">Level</th><th scope="col">Code</th><th scope="col" className="num">Count</th><th scope="col">Example</th></tr></thead>
              <tbody>
                {b.validation.map((v) => (
                  <tr key={v.severity + v.code}><td>{v.severity}</td><td className="mono">{v.code}</td>
                    <td className="num">{count(v.count)}</td><td>{v.message}{v.sample ? <span className="muted"> ({v.sample})</span> : null}</td></tr>
                ))}
              </tbody>
            </table>
          )}
      </div>
    </details>
  );
}

function ImportHistory() {
  const { data, error, reload } = useApi<Imports>("/api/data/imports", null);
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <p className="empty" aria-live="polite">Loading import history…</p>;
  return (
    <section className="card" aria-label="Import history">
      <header className="card-head"><h3>Import history</h3>
        <span className="muted">{count(data.total_batches)} batches{data.total_batches > data.batches.length ? ` (newest ${data.batches.length} shown)` : ""}</span></header>
      {data.batches.length === 0 && <p className="empty">Nothing imported yet. Run <code>azfinops-import</code>.</p>}
      {data.batches.map((b) => <BatchRow key={b.batch_id} b={b} />)}
      {data.price_sheet_files.length > 0 && (
        <>
          <h4>Price sheets</h4>
          <table className="data">
            <caption className="sr-only">Imported price sheet files</caption>
            <thead><tr><th scope="col">File</th><th scope="col">Imported</th><th scope="col" className="num">Rows loaded</th>
              <th scope="col" className="num">Rejected</th><th scope="col" className="num">Size</th></tr></thead>
            <tbody>
              {data.price_sheet_files.map((p) => (
                <tr key={p.file_name + p.imported_at}><td>{p.file_name}</td><td>{when(p.imported_at)}</td>
                  <td className="num">{p.rows_loaded === null ? "—" : count(p.rows_loaded)}</td>
                  <td className="num">{p.rows_rejected === null ? "—" : count(p.rows_rejected)}</td><td className="num">{bytes(p.bytes)}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <p className="muted small">
        File counts describe the original import, not rows still stored after retention. Purges are recorded in each
        affected batch's validation messages. Dashboard imports pause readers; command-line imports need the server stopped.
      </p>
    </section>
  );
}

function download(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

function Workbench() {
  const schema = useApi<SchemaListing>("/api/sql/schema", null);
  const [sql, setSql] = useState(EXAMPLES[0]?.[1] ?? "SELECT 1");
  const [result, setResult] = useState<SqlResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const ctl = useRef<AbortController | null>(null);
  useEffect(() => () => ctl.current?.abort(), []);

  const run = () => {
    if (running || !sql.trim()) return;
    const ac = new AbortController();
    ctl.current = ac;
    setRunning(true); setError(null);
    postJson<SqlResult>("/api/sql/run", { sql, limit: 1000 }, ac.signal)
      .then((r) => { if (ctl.current === ac) setResult(r); })
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setResult(null);
        setError(e instanceof ApiError || e instanceof Error ? e.message : String(e));
      })
      .finally(() => { if (ctl.current === ac) setRunning(false); });
  };
  const cancel = () => { ctl.current?.abort(); setRunning(false); setError("Cancelled."); };

  return (
    <section className="card" aria-label="SQL workbench">
      <header className="card-head"><h3>SQL workbench</h3>
        <span className="muted">read-only · one SELECT at a time · up to 1,000 rows · 20 s limit</span></header>
      <div className="wb">
        <aside className="wb-schema" aria-label="Tables and columns">
          {schema.error && <p className="error small">{schema.error}</p>}
          {schema.data?.tables.map((t) => (
            <details key={t.name}>
              <summary>
                <button type="button" className="link-btn mono" title="Use this table in the editor"
                  onClick={(e) => { e.preventDefault(); setSql(`SELECT *\nFROM ${t.name}\nLIMIT 100`); }}>{t.name}</button>
                {t.kind === "view" && <span className="muted small"> view</span>}
              </summary>
              <ul className="cols">{t.columns.map((c) => <li key={c.name}><span className="mono">{c.name}</span> <span className="muted small">{c.type}</span></li>)}</ul>
            </details>
          ))}
        </aside>
        <div className="wb-main">
          <label className="sr-only" htmlFor="sql-editor">SQL query</label>
          <textarea id="sql-editor" className="editor mono" value={sql} spellCheck={false} rows={8}
            onChange={(e) => setSql(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); } }} />
          <div className="filter-row">
            <button type="button" className="pick-btn" onClick={run} disabled={running || !sql.trim()}>{running ? "Running…" : "Run (Ctrl+Enter)"}</button>
            {running && <button type="button" className="pick-btn" onClick={cancel}>Cancel</button>}
            <label className="field"><span>Examples</span>
              <select value="" onChange={(e) => { const x = EXAMPLES.find(([n]) => n === e.target.value); if (x) setSql(x[1]); }}>
                <option value="">Choose…</option>
                {EXAMPLES.map(([n]) => <option key={n} value={n}>{n}</option>)}
              </select></label>
            {result && <button type="button" className="pick-btn" onClick={() => download("query-result.csv", toCsv(result.columns, result.rows))}>Download CSV</button>}
          </div>
          {error && <p className="banner error" role="alert">{error}</p>}
          {result && (
            <div aria-live="polite">
              <p className="muted small">
                {count(result.rows.length)} row{result.rows.length === 1 ? "" : "s"} in {Math.round(result.elapsed_ms)} ms
                {result.truncated && <strong> · stopped at {count(result.limit)} rows; add LIMIT or aggregate to see the rest</strong>}
              </p>
              <div className="table-scroll result">
                <table className="data">
                  <caption className="sr-only">Query result</caption>
                  <thead><tr>{result.columns.map((c, i) => <th key={c + i} scope="col" title={result.types[i]}>{c}</th>)}</tr></thead>
                  <tbody>
                    {result.rows.length === 0 && <tr><td colSpan={Math.max(1, result.columns.length)} className="empty">No rows.</td></tr>}
                    {result.rows.map((r, i) => (
                      <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === "number" ? "num" : undefined}>{v === null ? <span className="muted">null</span> : String(v)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export function DataManagement() {
  return (
    <div>
      <p className="muted small">Import more files, see what is in the database and whether it can be trusted, and look at it yourself with read-only SQL. Filters above do not apply here.</p>
      <ImportPanel />
      <QualityChecks />
      <ImportHistory />
      <Workbench />
    </div>
  );
}
