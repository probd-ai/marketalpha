"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

/** Symbol text box with search: matches ticker or company name against the symbols the desk accepts.
 *  Typing still works as before; a pick just fills the ticker. ↑/↓ move, Enter picks, Esc closes. */
export function SymbolInput({ value, onChange, className, placeholder, ariaLabel }: {
  value: string; onChange: (symbol: string) => void; className?: string; placeholder?: string; ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [q, setQ] = useState(value);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {                                   // debounce the search term
    const t = setTimeout(() => setQ(value.trim()), 180);
    return () => clearTimeout(t);
  }, [value]);
  useEffect(() => {                                   // close on outside click
    const h = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const hits = useQuery({ queryKey: ["symbols", q], queryFn: () => api.symbols(q), enabled: open && q.length > 0,
    staleTime: 5 * 60_000 });
  const rows = hits.data ?? [];
  const pick = (s: string) => { onChange(s); setOpen(false); };

  return (
    <div ref={box} className="relative">
      <input aria-label={ariaLabel} className={className} placeholder={placeholder} value={value} autoComplete="off"
        role="combobox" aria-expanded={open && rows.length > 0}
        onFocus={() => setOpen(true)}
        onChange={(e) => { onChange(e.target.value.toUpperCase()); setOpen(true); setActive(0); }}
        onKeyDown={(e) => {
          if (!open || !rows.length) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, rows.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter") { e.preventDefault(); pick(rows[Math.min(active, rows.length - 1)].symbol); }
          else if (e.key === "Escape") setOpen(false);
        }} />
      {open && q.length > 0 && (rows.length > 0 || hits.isFetched) && (
        <ul role="listbox" className="absolute left-0 z-20 mt-1 max-h-72 w-80 overflow-auto rounded-md border border-line bg-panel py-1 shadow-lg">
          {rows.length === 0 && <li className="px-3 py-1.5 text-[12.5px] text-muted">No symbol matches “{q}”</li>}
          {rows.map((r, i) => (
            <li key={r.symbol} role="option" aria-selected={i === active}
              className={`cursor-pointer px-3 py-1.5 text-[13px] ${i === active ? "bg-accent-soft" : "hover:bg-panel-2"}`}
              onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); pick(r.symbol); }}>
              <span className="font-semibold">{r.symbol}</span>
              {r.name !== r.symbol && <span className="ml-2 text-ink-2">{r.name}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
