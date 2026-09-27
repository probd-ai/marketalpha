"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { TicketTable } from "@/components/TicketTable";
import { Badge, Button, Card, Empty, ErrorBox, Loading } from "@/components/ui";
import { api, type Check } from "@/lib/api";
import { day, dayTime, qty, rupees } from "@/lib/format";

function CheckRow({ c }: { c: Check }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-line py-2 last:border-0">
      <div className="flex items-start gap-3">
        <Badge value={c.status} />
        <div className="flex-1">
          <div className="font-medium">{c.name}</div>
          <div className="text-[12.5px] text-ink-2">{c.detail}</div>
          {c.items.length > 0 && (
            <button className="text-[12px] text-accent hover:underline" onClick={() => setOpen(!open)}>
              {open ? "hide" : `show ${c.items.length} item${c.items.length > 1 ? "s" : ""}`}
            </button>
          )}
          {open && <pre className="mt-1 max-h-48 overflow-auto rounded bg-panel-2 p-2 text-[11.5px]">{JSON.stringify(c.items, null, 1)}</pre>}
        </div>
      </div>
    </li>
  );
}

export default function RunReview() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const router = useRouter();
  const q = useQuery({ queryKey: ["run", id], queryFn: () => api.run(id) });
  const [confirming, setConfirming] = useState(false);
  const refresh = () => { qc.invalidateQueries(); };
  const publish = useMutation({ mutationFn: () => api.publish(id), onSuccess: () => { setConfirming(false); refresh(); } });
  const discard = useMutation({ mutationFn: () => api.discard(id), onSuccess: () => { refresh(); router.push("/"); } });
  const ack = useMutation({ mutationFn: () => api.ackDrift(id), onSuccess: refresh });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data!;
  const blocked = r.checks.filter((c) => c.status === "BLOCK");
  const driftBlock = blocked.some((c) => c.name === "Drift");
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-[20px] font-semibold">Run {r._id}</h1>
            <Badge value={r.status} />
          </div>
          <div className="text-muted">
            {r.iso_week} · signal {day(r.signal_date)} → trade at the open of {day(r.fill_date)} (next session)
            {r.rebalance?.due ? ` · includes the fund rebalance (${r.rebalance.why})` : r.monthly_reset ? " · includes the fund rebalance" : ""} · model v{r.version} · engine {r.engine_version} · computed {dayTime(r.created_at)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 no-print">
          {r.status === "DRAFT" && !confirming && (
            <>
              <Button kind="danger" onClick={() => discard.mutate()} disabled={discard.isPending}>Discard</Button>
              <Button kind="primary" onClick={() => setConfirming(true)} disabled={blocked.length > 0}
                title={blocked.length ? "Blocked by pre-trade checks" : undefined}>Publish ticket</Button>
            </>
          )}
          {r.status !== "DRAFT" && r.status !== "DISCARDED" && (
            <Link href={`/runs/${r._id}/ticket`}><Button kind="primary">Open ticket & record executions</Button></Link>
          )}
        </div>
      </div>

      {r.initial_build && (
        <div className="rounded-lg border border-accent bg-accent-soft px-4 py-3 text-[13px]">
          <b>Initial build.</b> This is the first run of model version {r.version}: the ticket buys the whole model book at target weights.
          From the next run on, tickets carry only that week&apos;s changes.
        </div>
      )}
      {confirming && (
        <div className="rounded-lg border border-accent bg-accent-soft p-4">
          <div className="font-semibold">Publish this ticket?</div>
          <div className="mt-1 text-ink-2">
            Publishing freezes these {r.ticket.length} orders as this week&apos;s trades for {r.iso_week}. The model will assume they were
            filled at the open of {day(r.fill_date)}; record what you actually did on the ticket page.
          </div>
          <div className="mt-3 flex gap-2">
            <Button kind="primary" onClick={() => publish.mutate()} disabled={publish.isPending}>{publish.isPending ? "Publishing…" : "Confirm publish"}</Button>
            <Button onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        </div>
      )}
      <ErrorBox error={publish.error || discard.error || ack.error} />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Card title={`Pre-trade checks · ${r.verdict}`} className="xl:col-span-1">
          <ul>{r.checks.map((c) => <CheckRow key={c.name} c={c} />)}</ul>
          {driftBlock && r.status === "DRAFT" && (
            <div className="mt-3 rounded-md border border-warn bg-warn-soft p-3 text-[12.5px]">
              Data changed under a published week. Review the items, then acknowledge to continue; the next run will show drift as a warning.
              <div className="mt-2"><Button onClick={() => ack.mutate()} disabled={ack.isPending}>Acknowledge drift</Button></div>
            </div>
          )}
        </Card>
        <Card title="Ticket" className="xl:col-span-2" right={<a className="text-[12px] text-accent hover:underline no-print" href={`/api/fund/runs/${r._id}/ticket?format=csv`}>Download CSV</a>}>
          {r.ticket.length ? <TicketTable lines={r.ticket} /> : <Empty>No trades this week — every sleeve holds.</Empty>}
          {r.residuals.length > 0 && (
            <div className="mt-3 text-[12px] text-muted">
              {r.residuals.length} model change{r.residuals.length > 1 ? "s" : ""} below one share skipped:{" "}
              {r.residuals.map((x) => `${x.symbol} (${x.sleeve}, ${qty(x.model_shares)})`).join(", ")}
            </div>
          )}
        </Card>
      </div>

      <Card title="Fund on the signal day">
        <table className="data-table">
          <thead><tr><th>Sleeve</th><th className="r">Target</th><th className="r">Weight</th><th className="r">Value</th><th className="r">Holdings</th></tr></thead>
          <tbody>
            {r.books.map((b) => (
              <tr key={b.id}>
                <td>{b.label}</td><td className="r num">{b.target_weight.toFixed(0)}%</td><td className="r num">{b.weight.toFixed(1)}%</td>
                <td className="r num">{rupees(b.value)}</td><td className="r num">{b.holdings.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
