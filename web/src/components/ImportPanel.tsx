import { useEffect, useMemo, useState } from "react";
import { getJson, postJson, type DropFile, type ImportFiles, type ImportKind, type ImportStatus } from "../api";
import { count } from "../format";
import { useApi } from "../hooks";

const KIND_LABEL: Record<ImportKind, string> = { usage: "Usage and charges", pricesheet: "Price sheet" };
const MAX_IMPORT_FILES = 200;

function size(b: number): string {
  return b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(0)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`;
}

function elapsed(from: string | null, to: string | null): string {
  if (!from) return "";
  const s = Math.max(0, Math.round(((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 1000));
  return s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;
}

/**
 * Import more usage-and-charges exports or price sheets from the allowed folders. The server pauses the dashboard
 * while it writes (DuckDB allows one writer and no readers), then rebuilds the aggregates and reconnects.
 */
export function ImportPanel() {
  const list = useApi<ImportFiles>("/api/import/files", null);
  const [status, setStatus] = useState<ImportStatus | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [kind, setKind] = useState<ImportKind>("usage");
  const [costKind, setCostKind] = useState<"amortized" | "actual">("amortized");
  const [supersede, setSupersede] = useState(true);
  const [reimport, setReimport] = useState(false);
  const [fail, setFail] = useState<string | null>(null);

  // Poll the job: every 2 s while one runs, once otherwise (to pick up a job started in another tab).
  useEffect(() => {
    let stop = false;
    let t = 0;
    const tick = async () => {
      try {
        const r = await getJson<ImportStatus>("/api/import/status", null, new AbortController().signal);
        if (stop) return;
        setStatus(r.data);
        if (r.data.state === "running") t = window.setTimeout(tick, 2000);
      } catch { if (!stop) t = window.setTimeout(tick, 5000); }
    };
    void tick();
    return () => { stop = true; window.clearTimeout(t); };
  }, [status?.state === "running"]);

  const files = list.data?.files ?? [];
  const shown = useMemo(() => files.filter((f) => f.kind === kind || f.kind === null), [files, kind]);
  const running = status?.state === "running";
  const choose = (f: DropFile) => setPicked((p) => { const n = new Set(p); n.has(f.id) ? n.delete(f.id) : n.add(f.id); return n; });
  const chosen = shown.filter((f) => picked.has(f.id));
  const total = chosen.reduce((a, f) => a + f.bytes, 0);
  const tooManyFiles = chosen.length > MAX_IMPORT_FILES;

  async function start() {
    setFail(null);
    try {
      const r = await postJson<ImportStatus>("/api/import/start", {
        kind, files: chosen.map((f) => f.id), cost_kind: costKind, supersede: kind === "usage" && supersede, reimport: kind === "usage" && reimport,
      }, new AbortController().signal);
      setStatus(r);
      setPicked(new Set());
    } catch (e) {
      setFail(e instanceof Error ? e.message : "Could not start the import");
    }
  }

  return (
    <section className="card" aria-label="Import files">
      <header className="card-head"><h3>Import files</h3>
        <button type="button" onClick={list.reload} disabled={running}>Refresh list</button></header>

      {status && status.state !== "idle" && (
        <div className={`banner ${status.state === "failed" ? "error" : status.state === "running" ? "warn" : ""} imp-status`} role="status" aria-live="polite">
          {status.state === "running" && (
            <p><strong>Importing, {elapsed(status.started, null)} so far.</strong> {status.step}
              {status.current && <> · {status.current}: {count(status.rows_loaded)} rows{status.rows_rejected ? `, ${count(status.rows_rejected)} rejected` : ""}</>}
              <br /><span className="small">The rest of the dashboard is paused until the import finishes. Large exports take several minutes per GB.</span></p>
          )}
          {status.state === "done" && (
            <p><strong>Import finished</strong> in {elapsed(status.started, status.finished)}.{" "}
              <button type="button" onClick={() => window.location.reload()}>Reload the dashboard</button></p>
          )}
          {status.state === "failed" && <p><strong>Import failed:</strong> {status.error}{" "}
            {status.data_committed
              ? "Data was committed before a later step failed. Do not assume the import was rolled back."
              : "No new commit was confirmed. Check import history before retrying."}
            {status.recovery_required && " Cost screens stay unavailable until aggregate recovery. Stop the server, run azfinops-aggregate, then restart it."}
          </p>}
          {status.results.length > 0 && <ul className="small">{status.results.map((r, i) => <li key={i}>{r.summary}</li>)}</ul>}
          {status.log.length > 0 && (
            <details><summary className="small">Log ({status.log.length} lines)</summary><pre className="imp-log">{status.log.join("\n")}</pre></details>
          )}
        </div>
      )}

      <p className="muted small">
        Put the CSV files in {list.data?.folders.length ? list.data.folders.map((f, i) => <code key={f}>{i ? ", " : ""}{f}</code>) : "the import folder"}, then
        refresh. Only files in these folders can be imported from here; set AZFINOPS_IMPORT_DIRS to use other folders.
      </p>
      {kind === "usage" && <p className="banner warn" role="note">
        Successful cost imports keep only the latest 12 calendar months, anchored to the newest imported cost month.
        Older stored cost rows and derived figures are purged across all enrollments, cost bases and currencies.
        Source CSVs, price sheets, backups, settings and exchange rates are not deleted.
      </p>}

      <div className="imp-opts">
        <div className="seg" role="group" aria-label="File type">
          {(["usage", "pricesheet"] as const).map((k) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => { setKind(k); setPicked(new Set()); }}>{KIND_LABEL[k]}</button>
          ))}
        </div>
        {kind === "usage" && (
          <>
            <label className="field">Cost basis
              <select value={costKind} onChange={(e) => setCostKind(e.target.value as "amortized" | "actual")}>
                <option value="amortized">Amortized</option><option value="actual">Actual</option>
              </select></label>
            <label className="check" title="Include every previously loaded enrollment, currency and date, and every export partition.">
              <input type="checkbox" checked={supersede} onChange={(e) => setSupersede(e.target.checked)} />
              Replace overlapping batches</label>
            <label className="check"><input type="checkbox" checked={reimport} onChange={(e) => setReimport(e.target.checked)} disabled={!supersede} />
              Allow the same file again</label>
          </>
        )}
      </div>

      <div className="imp-opts" role="group" aria-label="File selection">
        <button type="button" disabled={running || chosen.length === shown.length}
          title="Select every file shown for this file type, including files marked imported."
          onClick={() => setPicked(new Set(shown.map((f) => f.id)))}>Select all</button>
        <button type="button" disabled={running || chosen.length === 0}
          onClick={() => setPicked(new Set())}>Clear selection</button>
        <span className="muted small" aria-live="polite">
          {count(chosen.length)} of {count(shown.length)} files selected
        </span>
      </div>
      {list.error && <p className="error small" role="alert">{list.error}</p>}
      {!list.data && !list.error && <p className="empty">Loading…</p>}
      {list.data && shown.length === 0 && <p className="muted small">No CSV files found in the import folders.</p>}
      {shown.length > 0 && (
        <div className="table-scroll imp-table">
          <table className="data">
            <thead><tr><th scope="col"><span className="sr-only">Choose</span></th><th scope="col">File</th><th scope="col">Type</th>
              <th scope="col" className="num">Size</th><th scope="col">Modified</th><th scope="col">Status</th></tr></thead>
            <tbody>
              {shown.map((f) => (
                <tr key={f.id}>
                  <td><input type="checkbox" checked={picked.has(f.id)} onChange={() => choose(f)} disabled={running} aria-label={`Choose ${f.name}`} /></td>
                  <td title={`${f.folder}\\${f.name}`}>{f.name}</td>
                  <td>{f.kind ? KIND_LABEL[f.kind] : <span className="muted">not recognised</span>}</td>
                  <td className="num">{size(f.bytes)}</td>
                  <td>{f.modified.replace("T", " ").slice(0, 16)}</td>
                  <td>{f.imported === null ? "—" : f.imported ? <span className="muted">imported</span> : "new"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {tooManyFiles && <p className="error small" role="alert">
        Choose no more than {MAX_IMPORT_FILES} files per import. Clear the selection or uncheck some files.
      </p>}

      <div className="imp-go">
        <button type="button" className="primary" disabled={running || chosen.length === 0 || tooManyFiles} onClick={() => void start()}>
          Import {chosen.length ? `${chosen.length} file${chosen.length > 1 ? "s" : ""} (${size(total)})` : ""}
        </button>
        <span className="muted small">
          {kind === "usage"
            ? "Each file is one batch, except partitions of one export (names ending _0, _1, …), which go together. Aggregates and names are rebuilt after each batch."
            : "Price sheet files are imported together; a period already loaded is replaced."}
        </span>
      </div>
      {fail && <p className="error small" role="alert">{fail}</p>}
    </section>
  );
}
