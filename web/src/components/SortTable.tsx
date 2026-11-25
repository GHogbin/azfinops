import { useMemo, useState, type ReactNode } from "react";

export interface Col<T> {
  key: string;
  header: string;
  /** Right-aligned numeric column. */
  num?: boolean;
  /** Value used for ordering; null always sorts last. */
  sortValue: (row: T) => number | string | null;
  render: (row: T) => ReactNode;
  info?: string;
}

interface Props<T> {
  caption: string;
  columns: Col<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  initialSort: { key: string; desc: boolean };
  footer?: ReactNode;
  empty?: string;
}

/** Client-side sortable table for lists that arrive complete (bounded by the API). */
export function SortTable<T>({ caption, columns, rows, rowKey, initialSort, footer, empty }: Props<T>) {
  const [sort, setSort] = useState(initialSort);
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const dir = sort.desc ? -1 : 1;
    return [...rows].sort((a, b) => {
      const x = col.sortValue(a);
      const y = col.sortValue(b);
      if (x === null && y === null) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
    });
  }, [rows, columns, sort]);

  const click = (key: string) => setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: true }));
  return (
    <div className="table-scroll">
      <table className="data">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.num ? "num" : undefined} title={c.info}
                aria-sort={sort.key === c.key ? (sort.desc ? "descending" : "ascending") : undefined}>
                <button type="button" className="th-btn" onClick={() => click(c.key)}>
                  {c.header}{sort.key === c.key && (sort.desc ? " ▼" : " ▲")}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && <tr><td colSpan={columns.length} className="empty">{empty ?? "Nothing to show."}</td></tr>}
          {sorted.map((r) => (
            <tr key={rowKey(r)}>
              {columns.map((c) => <td key={c.key} className={c.num ? "num" : undefined}>{c.render(r)}</td>)}
            </tr>
          ))}
        </tbody>
        {footer && <tfoot>{footer}</tfoot>}
      </table>
    </div>
  );
}
