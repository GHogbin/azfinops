import { useEffect, useMemo, useRef, useState } from "react";
import { filterParams, type Dim, type Filters, type Top } from "../api";
import { useApi } from "../hooks";
import { BarList } from "./BarList";

/**
 * A ranked list that asks the server for its rows only once it is near the screen, so the top of the Overview
 * loads without waiting for the lists below the chart.
 */
export function LazyTop({ dim, title, filters, onPick, onOpen }: {
  dim: Dim; title: string; filters: Filters; onPick: (v: string) => void; onOpen: (v: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(typeof IntersectionObserver === "undefined");
  useEffect(() => {
    if (seen || !box.current) return;
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) setSeen(true); }, { rootMargin: "300px" });
    io.observe(box.current);
    return () => io.disconnect();
  }, [seen]);
  const params = useMemo(() => { const p = filterParams(filters); p.set("dimension", dim); p.set("n", "10"); return p; }, [filters, dim]);
  const { data, error } = useApi<Top>(seen ? "/api/cost/top" : null, seen ? params : null);
  return (
    <div ref={box} className="lazy-top">
      {data ? (
        <BarList title={title} rows={data.rows} other={data.other} otherSplit={data.other_split} total={data.total} groups={data.groups}
          currency={filters.currency} onPick={onPick} onOpen={onOpen} />
      ) : (
        <section className="card" aria-label={title} aria-busy={!error}>
          <header className="card-head"><h3>{title}</h3></header>
          <p className="muted small">{error ? `Could not load: ${error}` : "Loading…"}</p>
        </section>
      )}
    </div>
  );
}
