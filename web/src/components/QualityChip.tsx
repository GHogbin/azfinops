import { useEffect, useState } from "react";
import { useApi } from "../hooks";

interface Checks { checks: { id: string; title: string; status: string }[] }

const KEEP_MS = 10 * 60 * 1000;
let cache: { at: number; data: Checks } | null = null;

/**
 * A header chip that appears only when a data-quality check warns or fails, linking to Data Management.
 * The checks run once, a moment after the page has loaded, and the result is kept for 10 minutes.
 */
export function QualityChip({ onOpen }: { onOpen: () => void }) {
  const [cached] = useState<Checks | null>(() => cache && Date.now() - cache.at < KEEP_MS ? cache.data : null);
  const [ready, setReady] = useState(false);
  const { data: loaded, error } = useApi<Checks>(!cached && ready ? "/api/data/quality" : null, null);
  const data = cached ?? loaded;
  useEffect(() => {
    if (cached) return;
    const t = window.setTimeout(() => setReady(true), 1500);
    return () => window.clearTimeout(t);
  }, [cached]);
  useEffect(() => {
    if (loaded) cache = { at: Date.now(), data: loaded };
  }, [loaded]);
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
