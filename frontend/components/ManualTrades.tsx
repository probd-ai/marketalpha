"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button, Card, ErrorBox } from "@/components/ui";
import { api } from "@/lib/api";
import { day, price, qty } from "@/lib/format";

const input = "rounded border border-line bg-panel px-2 py-1.5";

/** Trades made outside a ticket — e.g. the streak-watch overlay (sell a broken value-core holding, buy Grid-10's next
 *  pick). Tracking counts them; the model is not changed. Selling more than the sleeve holds is refused. */
export function ManualTrades() {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ["config"], queryFn: api.config });
  const list = useQuery({ queryKey: ["manual-trades"], queryFn: api.manualTrades });
  const [f, setF] = useState({ sleeve: "", symbol: "", side: "SELL" as "BUY" | "SELL", qty: "", price: "", note: "" });
  const add = useMutation({
    mutationFn: () => api.addManualTrade({ sleeve: f.sleeve, symbol: f.symbol.trim().toUpperCase(), side: f.side,
      qty: Number(f.qty), price: Number(f.price), note: f.note.trim() || undefined }),
    onSuccess: () => { setF({ ...f, symbol: "", qty: "", price: "", note: "" }); qc.invalidateQueries(); },
  });
  const del = useMutation({ mutationFn: (id: string) => api.deleteManualTrade(id), onSuccess: () => qc.invalidateQueries() });
  const sleeves = cfg.data?.sleeves.map((s) => s.label) ?? [];
  const sleeve = f.sleeve || sleeves[0] || "";
  const n = Number(f.qty), p = Number(f.price);
  const valid = sleeve && f.symbol.trim() && /^\d+$/.test(f.qty.trim()) && n > 0 && Number.isFinite(p) && p > 0;

  return (
    <Card title="Manual trades (outside a ticket)">
      <div className="mb-2 text-[12.5px] text-muted">
        For trades the desk did not ticket — e.g. a streak-watch exit and its Grid-10 replacement. Tracking counts them; the model and
        future tickets are not changed, so skip any later ticket line for a stock you already traded here.
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <select aria-label="sleeve" className={input} value={sleeve} onChange={(e) => setF({ ...f, sleeve: e.target.value })}>
          {sleeves.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select aria-label="side" className={input} value={f.side} onChange={(e) => setF({ ...f, side: e.target.value as "BUY" | "SELL" })}>
          <option value="SELL">SELL</option><option value="BUY">BUY</option>
        </select>
        <input aria-label="symbol" className={`${input} w-32 uppercase`} placeholder="MARUTI" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })} />
        <input aria-label="quantity" className={`${input} num w-24`} placeholder="qty" inputMode="numeric" value={f.qty} onChange={(e) => setF({ ...f, qty: e.target.value })} />
        <input aria-label="price" className={`${input} num w-28`} placeholder="price" inputMode="decimal" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} />
        <input aria-label="note" className={`${input} min-w-[12rem] flex-1`} placeholder="note, e.g. streak exit" maxLength={200} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
        <Button kind="primary" disabled={!valid || add.isPending} onClick={() => { setF({ ...f, sleeve }); add.mutate(); }}>Record</Button>
      </div>
      <div className="mt-2"><ErrorBox error={add.error ?? del.error} /></div>
      {(list.data ?? []).length > 0 && (
        <table className="data-table mt-3">
          <thead><tr><th>Date</th><th>Sleeve</th><th>Side</th><th>Symbol</th><th className="r">Qty</th><th className="r">Price</th><th>Note</th><th /></tr></thead>
          <tbody>
            {list.data!.map((t) => (
              <tr key={t._id}>
                <td className="num text-muted">{day(t.trade_date)}</td><td>{t.sleeve}</td><td>{t.side}</td>
                <td className="font-semibold">{t.symbol}</td><td className="r num">{qty(t.qty)}</td><td className="r num">{price(t.price)}</td>
                <td className="text-ink-2">{t.note}</td>
                <td><button className="text-[12px] text-bad hover:underline" onClick={() => { if (confirm(`Delete ${t.side} ${t.qty} ${t.symbol}?`)) del.mutate(t._id); }}>delete</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
