import { useEffect, useState } from "react";
import { getJson } from "../api";

interface Checks { checks: { id: string; title: string; status: string }[] }

const KEEP_MS = 10 * 60 * 1000;
let cache: { at: number; data: Checks } | null = null;

/**
 * A header chip that appears only when a data-quality check warns or fails, linking to Data Management.
 * The checks run once, a moment after the page has loaded, and the result is kept for 10 minutes.
 */
export function QualityChip({ onOpen }: { onOpen: () => void }) {
  const [data, setData] = useState<Checks | null>(cache && Date.now() - cache.at < KEEP_MS ? cache.data : null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (data) return;
    const c = new AbortController();
    const t = window.setTimeout(() => {
      getJson<Checks>("/api/data/quality", null, c.signal)
        .then((r) => { cache = { at: Date.now(), data: r.data }; setData(r.data); })
        .catch((e: unknown) => {
          if (!c.signal.aborted) setError(e instanceof Error ? e.message : String(e));
        });
    }, 1500);
    return () => { window.clearTimeout(t); c.abort(); };
  }, [data]);
  if (error) return <button type="button" className="chip-pill q-chip q-warn" onClick={onOpen} title={error}>
    Data checks unavailable
  </button>;
  if (!data) return null;
  const bad = data.checks.filter((x) => x.status === "warn" || x.status === "fail");
  if (bad.length === 0) return null;
  const fail = bad.some((x) => x.status === "fail");
  return (
    <button type="button" className={`chip-pill q-chip ${fail ? "q-fail" : "q-warn"}`} onClick={onOpen}
      title={bad.map((x) => x.title).join("\n")}>
      {bad.length} data {bad.length === 1 ? "warning" : "warnings"}
    </button>
  );
}
