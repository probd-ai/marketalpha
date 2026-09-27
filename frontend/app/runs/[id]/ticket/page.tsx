"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { TicketTable } from "@/components/TicketTable";
import { Badge, Button, Card, ErrorBox, Loading } from "@/components/ui";
import { api, type Execution, type TicketLine } from "@/lib/api";
import { day } from "@/lib/format";

type Draft = { status: "" | Execution["status"]; qty: string; price: string };

export default function TicketPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["run", id], queryFn: () => api.run(id) });
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    if (!q.data) return;
    const ex = Object.fromEntries((q.data.executions ?? []).map((e) => [e.line_id, e]));
    setDrafts(Object.fromEntries(q.data.ticket.map((l) => {
      const e = ex[l.line_id];
      return [l.line_id, { status: e?.status ?? "", qty: String(e?.qty ?? l.qty), price: e?.price ? String(e.price) : "" }];
    })));
  }, [q.data]);

  const save = useMutation({
    mutationFn: (items: Partial<Execution>[]) => api.record(id, items),
    onSuccess: (r) => { setSaved(`Saved · ticket ${r.status.toLowerCase()} (${r.recorded}/${r.lines} lines recorded)`); qc.invalidateQueries(); },
  });

  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data!;
  const editable = r.status !== "DRAFT" && r.status !== "DISCARDED";
  const set = (lid: string, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [lid]: { ...d[lid], ...patch } }));
  const pending = Object.entries(drafts).filter(([, d]) => d.status);
  const toItems = (entries: [string, Draft][]) => entries.map(([line_id, d]) => ({
    line_id, status: d.status as Execution["status"],
    ...(d.status === "partial" ? { qty: Number(d.qty) } : {}),
    ...(d.status !== "skipped" ? { price: Number(d.price) } : {}),
  }));

  const cell = (l: TicketLine) => {
    const d = drafts[l.line_id];
    if (!d) return null;
    if (!editable) return <span className="text-muted">publish first</span>;
    return (
      <div className="flex items-center gap-1.5 no-print">
        <select aria-label={`status ${l.line_id}`} value={d.status} onChange={(e) => set(l.line_id, { status: e.target.value as Draft["status"] })}
          className="rounded border border-line bg-panel px-1.5 py-1 text-[12.5px]">
          <option value="">—</option><option value="done">done</option><option value="partial">partial</option><option value="skipped">skipped</option>
        </select>
        {d.status === "partial" && (
          <input aria-label={`qty ${l.line_id}`} className="num w-16 rounded border border-line bg-panel px-1.5 py-1 text-[12.5px]" value={d.qty}
            onChange={(e) => set(l.line_id, { qty: e.target.value })} inputMode="numeric" placeholder="qty" />
        )}
        {(d.status === "done" || d.status === "partial") && (
          <input aria-label={`price ${l.line_id}`} className="num w-24 rounded border border-line bg-panel px-1.5 py-1 text-[12.5px]" value={d.price}
            onChange={(e) => set(l.line_id, { price: e.target.value })} inputMode="decimal" placeholder="fill price" />
        )}
        {(r.executions ?? []).some((e) => e.line_id === l.line_id) && <Badge value={(r.executions ?? []).find((e) => e.line_id === l.line_id)!.status} />}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><h1 className="text-[20px] font-semibold">Ticket {r._id}</h1><Badge value={r.status} /></div>
          <div className="text-muted">Trade at or after the open of {day(r.fill_date)} · sells first, then buys · limit = reference ± band</div>
        </div>
        <div className="flex gap-2 no-print">
          <Link href={`/runs/${r._id}`}><Button>Back to review</Button></Link>
          <a href={`/api/fund/runs/${r._id}/ticket?format=csv`}><Button>Download CSV</Button></a>
          <Button onClick={() => window.print()}>Print</Button>
        </div>
      </div>
      {editable && (
        <div className="flex flex-wrap items-center gap-3 no-print">
          <Button kind="primary" disabled={!pending.length || save.isPending} onClick={() => { setSaved(null); save.mutate(toItems(pending)); }}>
            {save.isPending ? "Saving…" : `Save ${pending.length} execution record${pending.length === 1 ? "" : "s"}`}
          </Button>
          <Button onClick={() => setDrafts((d) => Object.fromEntries(Object.entries(d).map(([k, v]) => {
            const l = r.ticket.find((t) => t.line_id === k)!;
            return [k, v.status ? v : { status: "done", qty: String(l.qty), price: String(l.ref_px) }];
          })))}>Mark unrecorded lines done at reference price</Button>
          {saved && <span className="text-good">{saved}</span>}
        </div>
      )}
      <ErrorBox error={save.error} />
      <Card><TicketTable lines={r.ticket} extra={cell} /></Card>
    </div>
  );
}
