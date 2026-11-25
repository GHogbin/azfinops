// A small in-memory log of the dashboard's requests: what is running now, and how long recent ones took.
// Nothing leaves the browser. Timings are the browser's own clock plus the server's Server-Timing header.
import { useSyncExternalStore } from "react";

export type Status = "running" | "ok" | "error" | "cancelled";
export interface Entry {
  id: number; method: string; path: string; query: string; label: string;
  start: number; ms: number | null; serverMs: number | null; dbMs: number | null; status: Status; http: number | null;
}

const NAMES: [RegExp, string][] = [
  [/^\/api\/overview/, "Overview"], [/^\/api\/cost\/explore/, "Cost Explorer table"], [/^\/api\/cost\/series/, "Cost over time"],
  [/^\/api\/cost\/movement/, "Month-on-month movement"], [/^\/api\/cost\/breakdown/, "Service meters"],
  [/^\/api\/dimensions/, "Filter values"], [/^\/api\/commitments/, "Commitments"], [/^\/api\/reservations/, "Reservations"],
  [/^\/api\/savings-headroom/, "Savings headroom"], [/^\/api\/applications/, "Applications"], [/^\/api\/workloads/, "AI & Database"],
  [/^\/api\/pricing/, "Pricing"], [/^\/api\/data\/imports/, "Import history"], [/^\/api\/data\/quality/, "Quality checks"],
  [/^\/api\/sql\/schema/, "SQL schema"], [/^\/api\/sql\/run/, "SQL workbench query"], [/^\/api\/fx\/combined/, "Conversion summary"],
  [/^\/api\/fx\/query/, "Exchange-rate lookup"], [/^\/api\/fx\/rates/, "Exchange rates"], [/^\/api\/meta/, "Scopes"],
];
export function labelFor(path: string): string {
  return NAMES.find(([re]) => re.test(path))?.[1] ?? path;
}

const KEEP = 40;
let entries: Entry[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => { entries = [...entries]; listeners.forEach((l) => l()); };

export function begin(method: string, path: string, query: string): number {
  const id = nextId++;
  entries.unshift({ id, method, path, query, label: labelFor(path), start: performance.now(), ms: null, serverMs: null,
                    dbMs: null, status: "running", http: null });
  if (entries.length > KEEP * 2) entries = entries.filter((e) => e.status === "running" || entries.indexOf(e) < KEEP);
  emit();
  return id;
}

export function finish(id: number, status: Exclude<Status, "running">, http: number | null,
  timing: { totalMs: number | null; dbMs: number | null } | null): void {
  const e = entries.find((x) => x.id === id);
  if (!e || e.status !== "running") return;
  e.ms = performance.now() - e.start;
  e.status = status; e.http = http;
  e.serverMs = timing?.totalMs ?? null; e.dbMs = timing?.dbMs ?? null;
  emit();
}

export function clearActivity(): void { entries = entries.filter((e) => e.status === "running"); emit(); }

/** Nearest-rank percentile of a list of numbers (0 to 1). */
export function percentile(values: number[], p: number): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.ceil(p * s.length) - 1))] ?? null;
}

export function summarize(list: Entry[]): { running: number; done: number; p50: number | null; p95: number | null; max: number | null; errors: number } {
  const done = list.filter((e) => e.status === "ok" && e.ms !== null);
  const ms = done.map((e) => e.ms as number);
  return { running: list.filter((e) => e.status === "running").length, done: done.length, p50: percentile(ms, 0.5),
           p95: percentile(ms, 0.95), max: ms.length ? Math.max(...ms) : null, errors: list.filter((e) => e.status === "error").length };
}

function subscribe(l: () => void): () => void { listeners.add(l); return () => { listeners.delete(l); }; }
export function useActivity(): Entry[] { return useSyncExternalStore(subscribe, () => entries); }
