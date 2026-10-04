"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { TicketTable } from "@/components/TicketTable";
import { Badge, Button, Card, ErrorBox, Loading } from "@/components/ui";
import { api, priceWarnings, type ExecStatus, type ExecutionItem, type PriceWarning, type TicketLine } from "@/lib/api";
import { draftFor, needsQty, toItem, type Draft } from "@/lib/executions";
import { day, price } from "@/lib/format";

export default function TicketPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["run", id], queryFn: () => api.run(id) });
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [review, setReview] = useState<{ items: ExecutionItem[]; warnings: PriceWarning[] } | null>(null);

  useEffect(() => {
    if (!q.data) return;
    const ex = Object.fromEntries((q.data.executions ?? []).map((e) => [e.line_id, e]));
    setDrafts(Object.fromEntries(q.data.ticket.map((l) => [l.line_id, draftFor(l, ex[l.line_id])])));
  }, [q.data]);

  const save = useMutation({
    mutationFn: ({ items, confirm }: { items: ExecutionItem[]; confirm: boolean }) => api.record(id, items, confirm),
    onSuccess: (r) => {
      setReview(null);
      setSaved(`Saved · ticket ${r.status.toLowerCase()} (${r.recorded}/${r.lines} lines recorded)`);
      qc.invalidateQueries();
    },
    onError: (e, v) => { const w = priceWarnings(e); if (w) setReview({ items: v.items, warnings: w }); },
  });

  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data!;
  const editable = r.status !== "DRAFT" && r.status !== "DISCARDED";
  const set = (lid: string, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [lid]: { ...d[lid], ...patch } }));
  const recorded = (lid: string) => (r.executions ?? []).find((e) => e.line_id === lid);
  // only lines that differ from what is already recorded — re-saving a confirmed line would re-raise its warning
  const pending = Object.entries(drafts).filter(([lid, d]) => {
    if (!d.status) return false;
    const e = recorded(lid);
    if (!e) return true;
    const l = r.ticket.find((t) => t.line_id === lid)!;
    return JSON.stringify(draftFor(l, e)) !== JSON.stringify(d);
  });

  const submit = () => {
    setSaved(null);
    setReview(null);
    const res = pending.map(([lid, d]) => toItem(r.ticket.find((t) => t.line_id === lid)!, d));
    const errs = res.flatMap((x) => ("error" in x ? [x.error] : []));
    setProblems(errs);
    if (!errs.length) save.mutate({ items: res.map((x) => (x as { item: ExecutionItem }).item), confirm: false });
  };

  const cell = (l: TicketLine) => {
    const d = drafts[l.line_id];
    if (!d) return null;
    if (!editable) return <span className="text-muted">publish first</span>;
    const rec = recorded(l.line_id);
    return (
      <div className="flex items-center gap-1.5 no-print">
        <select aria-label={`status ${l.line_id}`} value={d.status}
          onChange={(e) => {
            const status = e.target.value as Draft["status"];
            set(l.line_id, { status, ...(status === "extra" && Number(d.qty) <= l.qty ? { qty: "" } : {}) });
          }}
          className="rounded border border-line bg-panel px-1.5 py-1 text-[12.5px]">
          <option value="">—</option><option value="done">done</option><option value="partial">partial</option>
          {l.side === "BUY" && <option value="extra">extra</option>}<option value="skipped">skipped</option>
        </select>
        {needsQty(d.status) && (
          <input aria-label={`qty ${l.line_id}`} className="num w-16 rounded border border-line bg-panel px-1.5 py-1 text-[12.5px]" value={d.qty}
            onChange={(e) => set(l.line_id, { qty: e.target.value })} inputMode="numeric"
            placeholder={d.status === "extra" ? `> ${l.qty}` : "qty"} title={d.status === "extra" ? "total shares bought; the fund counts the ticket quantity, the rest goes to Other holdings" : undefined} />
        )}
        {d.status && d.status !== "skipped" && (
          <input aria-label={`price ${l.line_id}`} className="num w-24 rounded border border-line bg-panel px-1.5 py-1 text-[12.5px]" value={d.price}
            onChange={(e) => set(l.line_id, { price: e.target.value })} inputMode="decimal" placeholder="fill price" />
        )}
        {rec && <Badge value={rec.status as ExecStatus} />}
        {rec?.price_warning && <span className="text-[11px] text-warn" title={rec.price_warning}>⚠ price reviewed</span>}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><h1 className="text-[20px] font-semibold">Ticket {r._id}</h1><Badge value={r.status} /></div>
          <div className="text-muted">Trade at or after the open of {day(r.fill_date)} · sells first, then buys · limit = reference ± band</div>
          <div className="text-[12.5px] text-muted"><b>extra</b> = you bought more than the ticket; the fund counts the ticket quantity and the rest shows under Other holdings.</div>
        </div>
        <div className="flex gap-2 no-print">
          <Link href={`/runs/${r._id}`}><Button>Back to review</Button></Link>
          <a href={`/api/fund/runs/${r._id}/ticket?format=csv`}><Button>Download CSV</Button></a>
          <Button onClick={() => window.print()}>Print</Button>
        </div>
      </div>
      {editable && (
        <div className="flex flex-wrap items-center gap-3 no-print">
          <Button kind="primary" disabled={!pending.length || save.isPending} onClick={submit}>
            {save.isPending ? "Saving…" : `Save ${pending.length} execution record${pending.length === 1 ? "" : "s"}`}
          </Button>
          <Button onClick={() => setDrafts((d) => Object.fromEntries(Object.entries(d).map(([k, v]) => {
            const l = r.ticket.find((t) => t.line_id === k)!;
            return [k, v.status ? v : { status: "done", qty: String(l.qty), price: String(l.ref_px) }];
          })))}>Mark unrecorded lines done at reference price</Button>
          {saved && <span className="text-good">{saved}</span>}
        </div>
      )}
      {problems.length > 0 && (
        <div className="rounded border border-bad bg-bad-soft px-3 py-2 text-[13px] text-bad" role="alert">
          {problems.map((p) => <div key={p}>{p}</div>)}
        </div>
      )}
      {review && (
        <div className="flex flex-col gap-2 rounded border border-warn bg-warn-soft px-3 py-2 text-[13px] no-print" role="alert">
          <div className="font-semibold text-warn">Review {review.warnings.length} fill price{review.warnings.length === 1 ? "" : "s"} before saving</div>
          <table className="data-table">
            <thead><tr><th>#</th><th>Symbol</th><th className="r">Your price</th><th className="r">Reference</th><th className="r">Traded since fill</th><th>Why</th></tr></thead>
            <tbody>
              {review.warnings.map((w) => (
                <tr key={w.line_id}>
                  <td className="num text-muted">{w.line_id}</td><td className="font-semibold">{w.symbol}</td>
                  <td className="r num">{price(w.price)}</td><td className="r num">{price(w.ref_px)}</td>
                  <td className="r num">{w.low !== null && w.high !== null ? `${price(w.low)} – ${price(w.high)}` : "—"}</td>
                  <td className="text-[12.5px] text-ink-2">{w.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex gap-2">
            <Button kind="primary" disabled={save.isPending} onClick={() => save.mutate({ items: review.items, confirm: true })}>Save anyway</Button>
            <Button onClick={() => setReview(null)}>Go back and edit</Button>
          </div>
        </div>
      )}
      {!review && <ErrorBox error={save.error} />}
      <Card><TicketTable lines={r.ticket} extra={cell} /></Card>
    </div>
  );
}
