"use client";

import type { TicketLine } from "@/lib/api";
import { price, qty, rupees } from "@/lib/format";

import { Badge } from "./ui";

/** Ticket lines grouped by sleeve, sells before buys (the order to execute them in). */
export function TicketTable({ lines, extra }: { lines: TicketLine[]; extra?: (l: TicketLine) => React.ReactNode }) {
  const sleeves = [...new Set(lines.map((l) => l.sleeve))];
  const sells = lines.filter((l) => l.side === "SELL").reduce((a, l) => a + l.value, 0);
  const buys = lines.filter((l) => l.side === "BUY").reduce((a, l) => a + l.value, 0);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-5 text-[13px]">
        <span><span className="text-muted">Lines</span> <b className="num">{lines.length}</b></span>
        <span><span className="text-muted">Sell value</span> <b className="num text-sell">{rupees(sells)}</b></span>
        <span><span className="text-muted">Buy value</span> <b className="num text-buy">{rupees(buys)}</b></span>
        <span><span className="text-muted">Net cash</span> <b className="num">{rupees(sells - buys)}</b></span>
      </div>
      {sleeves.map((s) => {
        const rows = lines.filter((l) => l.sleeve === s);
        return (
          <div key={s}>
            <div className="mb-1 text-[12.5px] font-semibold uppercase tracking-wide text-ink-2">{s} · {rows.length} line{rows.length > 1 ? "s" : ""}</div>
            <div className="overflow-x-auto">
              <table className="grid">
                <thead>
                  <tr><th>#</th><th>Side</th><th>Symbol</th><th className="r">Qty</th><th className="r">Ref price</th><th className="r">Limit</th>
                    <th className="r">Value</th><th>Why</th>{extra && <th>Execution</th>}</tr>
                </thead>
                <tbody>
                  {rows.map((l) => (
                    <tr key={l.line_id}>
                      <td className="text-muted num">{l.line_id}</td>
                      <td><Badge value={l.side} /></td>
                      <td className="font-semibold">{l.symbol}{l.full_exit && <span className="ml-1 text-[11px] text-muted">(exit all)</span>}</td>
                      <td className="r num">{qty(l.qty)}</td>
                      <td className="r num">{price(l.ref_px)}</td>
                      <td className="r num">{price(l.limit_px)}</td>
                      <td className="r num">{rupees(l.value, { compact: false })}</td>
                      <td className="max-w-[420px] text-[12.5px] text-ink-2">{l.reason_text}</td>
                      {extra && <td>{extra(l)}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}
