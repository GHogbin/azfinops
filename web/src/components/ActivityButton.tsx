import { useEffect, useId, useState } from "react";
import { clearActivity, summarize, useActivity, type Entry } from "../activity";

const ms = (v: number | null): string => (v === null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);

function Row({ e, now }: { e: Entry; now: number }) {
  const elapsed = e.status === "running" ? now - e.start : e.ms;
  const q = e.query.length > 140 ? `${e.query.slice(0, 140)}…` : e.query;
  return (
    <li className={`act-row act-${e.status}`}>
      <div className="act-top">
        <span className="act-dot" aria-hidden="true" />
        <strong>{e.label}</strong>
        <span className="act-ms">{ms(elapsed)}</span>
      </div>
      <div className="act-sub muted small">
        {e.status === "running" ? "running" : e.status === "ok" ? `done${e.dbMs !== null ? ` · database ${ms(e.dbMs)}` : ""}${e.serverMs !== null ? ` · server ${ms(e.serverMs)}` : ""}`
          : e.status === "cancelled" ? "cancelled (superseded or navigated away)" : `failed${e.http ? ` (HTTP ${e.http})` : ""}`}
      </div>
      <code className="act-q small">{e.method} {e.path}{q ? `?${q}` : ""}</code>
    </li>
  );
}

/** Header button: how busy the dashboard is, with a panel (on hover, focus or click) listing running and recent queries. */
export function ActivityButton() {
  const all = useActivity();
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [now, setNow] = useState(() => performance.now());
  const id = useId();
  const open = hover || pinned;
  const running = all.filter((e) => e.status === "running");
  const recent = all.filter((e) => e.status !== "running").slice(0, 12);
  const sum = summarize(all);

  useEffect(() => {
    if (!open && running.length === 0) return;
    const t = window.setInterval(() => setNow(performance.now()), 200);
    return () => window.clearInterval(t);
  }, [open, running.length]);
  useEffect(() => {
    if (!pinned) return;
    const onKey = (ev: KeyboardEvent) => { if (ev.key === "Escape") setPinned(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pinned]);

  const last = recent.find((e) => e.status === "ok");
  return (
    <div className="activity" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button type="button" className={running.length ? "link-btn act-btn busy" : "link-btn act-btn"} aria-expanded={open}
        aria-controls={id} onClick={() => setPinned((p) => !p)}
        aria-label={`Activity: ${running.length} running${last ? `, last query ${ms(last.ms)}` : ""}. Show query details.`}>
        <span className="act-pulse" aria-hidden="true" />
        {running.length > 0 ? `${running.length} running` : last ? ms(last.ms) : "Activity"}
      </button>
      {open && (
        <section id={id} className="act-panel" aria-label="Query activity">
          <header className="act-head">
            <strong>Query activity</strong>
            <button type="button" className="link-btn small" onClick={clearActivity}>Clear</button>
          </header>
          <p className="muted small act-stats">
            {sum.done === 0 ? "No finished queries yet." : `Last ${sum.done}: median ${ms(sum.p50)} · 95th percentile ${ms(sum.p95)} · slowest ${ms(sum.max)}`}
            {sum.errors > 0 ? ` · ${sum.errors} failed` : ""}
          </p>
          <h4>Running now ({running.length})</h4>
          {running.length === 0 ? <p className="muted small">Nothing is running.</p> : <ul className="act-list">{running.map((e) => <Row key={e.id} e={e} now={now} />)}</ul>}
          <h4>Recent</h4>
          {recent.length === 0 ? <p className="muted small">Nothing yet.</p> : <ul className="act-list">{recent.map((e) => <Row key={e.id} e={e} now={now} />)}</ul>}
          <p className="muted small">Times are measured in your browser; “server” and “database” come from the API’s Server-Timing header.</p>
        </section>
      )}
    </div>
  );
}
