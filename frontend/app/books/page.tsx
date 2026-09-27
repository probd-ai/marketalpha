"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { type Column, SortTable } from "@/components/SortTable";
import { Card, Empty, ErrorBox, Loading, Signed } from "@/components/ui";
import { api, type Book, type Holding } from "@/lib/api";
import { day, pct, price, qty, rupees } from "@/lib/format";

function columns(b: Book): Column<Holding>[] {
  const cols: Column<Holding>[] = [
    { key: "symbol", label: "Symbol", render: (h) => <span className="font-semibold">{h.symbol}</span> },
    { key: "weight", label: "Weight", align: "right", render: (h) => pct(h.weight) },
    { key: "value", label: "Value", align: "right", render: (h) => rupees(h.value) },
    { key: "shares", label: "Shares", align: "right", render: (h) => qty(h.shares) },
    { key: "price", label: "Close", align: "right", render: (h) => price(h.price) },
    { key: "roc22", label: "22-day", align: "right", render: (h) => <Signed v={h.roc22}>{pct(h.roc22, 1, true)}</Signed> },
    { key: "held_since", label: "Held since", render: (h) => day(h.held_since) },
  ];
  if (b.kind === "grid10") cols.push({ key: "grid", label: "Grid", render: (h) => (h.grid === 1 ? "up" : h.grid === -1 ? "down" : "—") });
  if (b.kind === "tranche") {
    cols.push({ key: "lots", label: "Tranches", align: "right", render: (h) => String(h.lots) });
    cols.push({ key: "oldest_age", label: "Oldest (wks)", align: "right", render: (h) => String(h.oldest_age) });
    cols.push({ key: "alpha", label: "Alpha now", value: (h) => (h.alpha ? 1 : 0), render: (h) => (h.alpha ? "yes" : "no") });
  }
  return cols;
}

const NOTE: Record<string, string> = {
  grid10: "Grid-10: sells only when the grid turns down AND the 22-day return is positive; winners are never trimmed.",
  tranche: "Weekly tranches: 2.5% slices held 8 weekly checks; holdings capped at 10% and trimmed weekly.",
  asset: "Held whole; resized only at the monthly sleeve reset.",
};

export default function Books() {
  const q = useQuery({ queryKey: ["books"], queryFn: api.books });
  const [tab, setTab] = useState(0);
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  if (!d.books.length) return <Empty>No model yet — compute a run on the Desk.</Empty>;
  const b = d.books[Math.min(tab, d.books.length - 1)];
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-[20px] font-semibold">Model books</h1>
        <div className="text-muted">As of the close of {day(d.signal_date)} (run {d.run_id}) · rupee amounts for the fund&apos;s capital</div>
      </div>
      <div className="flex flex-wrap gap-1 border-b border-line">
        {d.books.map((x, i) => (
          <button key={x.id} onClick={() => setTab(i)}
            className={`-mb-px border-b-2 px-3 py-1.5 text-[13.5px] ${i === tab ? "border-accent font-semibold text-accent" : "border-transparent text-ink-2 hover:text-ink"}`}>
            {x.label} <span className="text-muted">({x.holdings.length})</span>
          </button>
        ))}
      </div>
      <Card title={`${b.label} · ${rupees(b.value)} · ${pct(b.weight)} of fund (target ${pct(b.target_weight, 0)})`}
        right={<span className="text-[12px] text-muted">cash {rupees(b.cash)}</span>}>
        <div className="mb-3 text-[12.5px] text-ink-2">{NOTE[b.kind]}</div>
        <SortTable rows={b.holdings} columns={columns(b)} rowKey={(h) => h.symbol} initialSort={{ key: "value", dir: -1 }} />
      </Card>
    </div>
  );
}
