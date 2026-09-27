"use client";

import { useMemo, useState, type ReactNode } from "react";

export interface Column<T> {
  key: string;
  label: ReactNode;
  value?: (row: T) => number | string | null | undefined;   // sort key; defaults to row[key]
  render?: (row: T) => ReactNode;
  align?: "right";
}

export function SortTable<T>({ rows, columns, initialSort, rowKey, empty = "Nothing to show" }: {
  rows: T[]; columns: Column<T>[]; initialSort?: { key: string; dir: 1 | -1 }; rowKey: (r: T) => string; empty?: ReactNode;
}) {
  const [sort, setSort] = useState(initialSort ?? null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    const get = col?.value ?? ((r: T) => (r as Record<string, unknown>)[sort.key] as number | string);
    return [...rows].sort((a, b) => {
      const x = get(a), y = get(b);
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * sort.dir;
    });
  }, [rows, columns, sort]);
  if (!rows.length) return <div className="py-6 text-center text-muted">{empty}</div>;
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.align === "right" ? "r" : ""}>
                <button className="uppercase hover:text-ink" onClick={() =>
                  setSort((s) => (s && s.key === c.key ? { key: c.key, dir: (s.dir * -1) as 1 | -1 } : { key: c.key, dir: -1 }))}>
                  {c.label}{sort?.key === c.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={rowKey(r)}>
              {columns.map((c) => (
                <td key={c.key} className={c.align === "right" ? "r num" : ""}>
                  {c.render ? c.render(r) : String((r as Record<string, unknown>)[c.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
