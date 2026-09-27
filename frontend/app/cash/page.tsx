"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Card, Empty, ErrorBox, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { dayTime, rupees } from "@/lib/format";

const input = "rounded border border-line bg-panel px-2 py-1.5 num";

export default function Cash() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const flows = useQuery({ queryKey: ["cashflows"], queryFn: api.cashflows });
  const [kind, setKind] = useState<"deposit" | "withdrawal">("deposit");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState(false);
  const add = useMutation({
    mutationFn: () => api.addCashflow((kind === "deposit" ? 1 : -1) * Number(amount), note),
    onSuccess: () => { setAmount(""); setNote(""); setConfirm(false); qc.invalidateQueries(); },
  });
  const cancel = useMutation({ mutationFn: (id: string) => api.cancelCashflow(id), onSuccess: () => qc.invalidateQueries() });
  if (status.isLoading || flows.isLoading) return <Loading />;
  if (status.error || flows.error) return <ErrorBox error={status.error || flows.error} />;
  const live = !!status.data!.last_published;
  const n = Number(amount);
  const valid = amount !== "" && Number.isFinite(n) && n > 0;
  const pending = flows.data!.filter((f) => f.status === "PENDING");

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="text-[20px] font-semibold">Cash: deposits and withdrawals</h1>
        <div className="text-muted">
          Money added or taken out after go-live. A recorded amount stays <b>pending</b> until the next run is published; that run&apos;s
          ticket spreads a deposit across the sleeves at target weights, or sells a withdrawal pro-rata. Flows buy or redeem fund units at
          the model&apos;s value, so they change the fund&apos;s size but never its returns.
        </div>
      </div>

      {!live ? (
        <Card title="Not live yet">
          <div className="text-ink-2">The fund goes live when the first ticket is published. Until then, set the amount you will invest as
            <b> Capital</b> in Settings.</div>
        </Card>
      ) : (
        <Card title="Record a cash flow">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">Type</span>
              <select className={input} value={kind} onChange={(e) => setKind(e.target.value as "deposit" | "withdrawal")}>
                <option value="deposit">Deposit</option><option value="withdrawal">Withdrawal</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">Amount (₹)</span>
              <input className={`${input} w-40`} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="500000" />
            </label>
            <label className="flex flex-1 flex-col gap-1">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">Note</span>
              <input className={input} value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
            </label>
            {!confirm && <Button kind="primary" disabled={!valid} onClick={() => setConfirm(true)}>Record</Button>}
          </div>
          {valid && <div className="mt-2 text-[12.5px] text-muted">{kind === "deposit" ? "Deposit" : "Withdrawal"} of {rupees(n, { compact: false })}</div>}
          {confirm && (
            <div className="mt-3 rounded-md border border-accent bg-accent-soft p-3">
              Record a {kind} of <b>{rupees(n, { compact: false })}</b>? It will be applied by the next published run.
              <div className="mt-2 flex gap-2">
                <Button kind="primary" onClick={() => add.mutate()} disabled={add.isPending}>Confirm</Button>
                <Button onClick={() => setConfirm(false)}>Cancel</Button>
              </div>
            </div>
          )}
          <div className="mt-2"><ErrorBox error={add.error} /></div>
          {pending.length > 0 && <div className="mt-2 text-warn">Compute a new run — its ticket will include the pending amount.</div>}
        </Card>
      )}

      <Card title="History (current model version)">
        {flows.data!.length ? (
          <table className="data-table">
            <thead><tr><th>Recorded</th><th>Type</th><th className="r">Amount</th><th>Note</th><th>Status</th><th>Applied by</th><th className="r">Units</th><th /></tr></thead>
            <tbody>
              {flows.data!.map((f) => (
                <tr key={f._id}>
                  <td className="text-muted">{dayTime(f.created_at)}</td>
                  <td>{f.amount > 0 ? "Deposit" : "Withdrawal"}</td>
                  <td className="r num">{rupees(f.amount, { compact: false })}</td>
                  <td className="text-ink-2">{f.note}</td>
                  <td><Badge value={f.status === "APPLIED" ? "PASS" : f.status === "PENDING" ? "DRAFT" : "DISCARDED"} label={f.status} /></td>
                  <td className="text-muted">{f.applied_run_id ?? "—"}</td>
                  <td className="r num">{f.units ? f.units.toLocaleString("en-IN", { maximumFractionDigits: 0 }) : "—"}</td>
                  <td>{f.status === "PENDING" && <Button onClick={() => cancel.mutate(f._id)} disabled={cancel.isPending}>Cancel</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>No deposits or withdrawals recorded.</Empty>}
        <ErrorBox error={cancel.error} />
      </Card>
    </div>
  );
}
