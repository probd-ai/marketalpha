"use client";

import type { Cell, PaperSection } from "@/lib/api";

/** Renders a structured AI Broadcast paper. Everything is plain text — React escapes it; nothing is parsed as HTML. */
const fmt = (c: Cell) => (c === null || c === undefined ? "—" : typeof c === "number" ? c.toLocaleString("en-IN") : c);

export function PaperView({ sections }: { sections: PaperSection[] }) {
  return (
    <div className="flex flex-col gap-6">
      {sections.map((s, i) => (
        <section key={i} className="flex flex-col gap-2">
          <h2 className="text-[16px] font-semibold">{s.heading}</h2>
          {s.body?.split(/\n\s*\n/).map((p, j) => (
            <p key={j} className="whitespace-pre-line text-[13.5px] leading-relaxed text-ink-2">{p}</p>
          ))}
          {s.note && (
            <div role="note" className={`rounded border px-3 py-2 text-[13px] ${s.note.tone === "warn"
              ? "border-warn bg-warn-soft text-warn" : "border-accent bg-accent-soft text-ink-2"}`}>
              {s.note.tone === "warn" ? "⚠ " : ""}{s.note.text}
            </div>
          )}
          {s.table && (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>{s.table.columns.map((c, j) => <th key={j} className={s.table!.align?.[j] === "right" ? "r" : ""}>{c}</th>)}</tr>
                </thead>
                <tbody>
                  {s.table.rows.map((r, k) => (
                    <tr key={k}>{r.map((c, j) => (
                      <td key={j} className={s.table!.align?.[j] === "right" ? "r num" : ""}>{fmt(c)}</td>
                    ))}</tr>
                  ))}
                  {s.table.total && (
                    <tr className="font-semibold">{s.table.total.map((c, j) => (
                      <td key={j} className={s.table!.align?.[j] === "right" ? "r num" : ""}>{fmt(c)}</td>
                    ))}</tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
