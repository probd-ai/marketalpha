/** Ticket-line execution drafts → API payloads. The server is the authority; this only shapes and pre-checks input. */
import type { ExecStatus, Execution, ExecutionItem, TicketLine } from "./api";

export type Draft = { status: "" | ExecStatus; qty: string; price: string };

export function draftFor(l: TicketLine, e: Execution | undefined): Draft {
  return { status: e?.status ?? "", qty: String(e?.qty ?? l.qty), price: e?.price ? String(e.price) : "" };
}

/** Whether the status needs a typed quantity (done and skipped take it from the ticket). */
export const needsQty = (s: Draft["status"]) => s === "partial" || s === "extra";

export function toItem(l: TicketLine, d: Draft): { item: ExecutionItem } | { error: string } {
  const id = l.line_id;
  if (!d.status) return { error: `${id}: choose a status` };
  if (d.status === "extra" && l.side !== "BUY") return { error: `${id}: extra applies to BUY lines only` };
  const item: ExecutionItem = { line_id: id, status: d.status };
  if (needsQty(d.status)) {
    const q = d.qty.trim();
    if (!/^\d+$/.test(q)) return { error: `${id}: quantity must be a whole number` };
    const n = Number(q);
    if (d.status === "partial" && !(n > 0 && n < l.qty)) return { error: `${id}: partial must be between 1 and ${l.qty - 1}` };
    if (d.status === "extra" && !(n > l.qty)) return { error: `${id}: extra must be more than the ticket's ${l.qty}` };
    item.qty = n;
  }
  if (d.status !== "skipped") {
    const p = Number(d.price.trim());
    if (d.price.trim() === "" || !Number.isFinite(p) || p <= 0) return { error: `${id}: a fill price is required` };
    item.price = p;
  }
  return { item };
}
