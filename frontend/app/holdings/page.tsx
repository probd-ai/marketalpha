"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { SortTable } from "@/components/SortTable";
import { SymbolInput } from "@/components/SymbolInput";
import { Button, Card, Empty, ErrorBox, Kpi, Loading } from "@/components/ui";
import { api, type OtherRow } from "@/lib/api";
import { price, qty, rupees } from "@/lib/format";

const input = "rounded border border-line bg-panel px-2 py-1.5";

/** Holdings outside the fund's books — so the desk (and AI review) sees the whole position at rebalance time. */
export default function OtherHoldings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["other-holdings"], queryFn: api.otherHoldings });
  const cfg = useQuery({ queryKey: ["config"], queryFn: api.config });
  const [sym, setSym] = useState("");
  const [shares, setShares] = useState("");
  const [cost, setCost] = useState("");
  const [note, setNote] = useState("");
  const [sleeve, setSleeve] = useState("");
  const save = useMutation({
    mutationFn: (body: Parameters<typeof api.saveOtherHoldings>[0]) => api.saveOtherHoldings(body),
    onSuccess: () => { setSym(""); setShares(""); setCost(""); setNote(""); setSleeve(""); qc.invalidateQueries(); },
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const n = Number(shares), c = Number(cost);
  const valid = sym.trim() !== "" && /^\d+$/.test(shares.trim()) && (cost.trim() === "" || (Number.isFinite(c) && c > 0));
  const edit = (o: OtherRow) => { setSym(o.symbol); setShares(String(o.qty)); setCost(o.cost_basis ? String(o.cost_basis) : ""); setNote(o.note ?? ""); setSleeve(o.sleeve ?? ""); };
  const sleeves = (cfg.data?.sleeves ?? []).map((s) => s.label);

  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <div>
        <h1 className="text-[20px] font-semibold">Other holdings</h1>
        <div className="text-muted">
          What you hold outside the fund&apos;s books — old positions, long-term holds, anything else. Never part of fund NAV;
          recorded so rebalance decisions (yours or an AI review) see your whole position. Tag a holding to a sleeve and Tracking counts
          its value toward that sleeve&apos;s capital, so the gap shows what is really still to deploy.
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Kpi label="Value" value={rupees(d.value)} sub="at the latest close" />
        <Kpi label="Recorded here" value={d.manual.length} />
        <Kpi label="Extra from tickets" value={d.from_tickets.length} sub="surplus of 'extra' fills" />
      </div>

      <Card title="Add or update a holding">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">Symbol</span>
            <SymbolInput className={`${input} w-36 uppercase`} value={sym} onChange={setSym} placeholder="search…" ariaLabel="symbol" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">Shares</span>
            <input className={`${input} num w-28`} inputMode="numeric" value={shares} onChange={(e) => setShares(e.target.value)} placeholder="0 removes" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">Avg cost</span>
            <input className={`${input} num w-28`} inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} placeholder="optional" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">Sleeve</span>
            <select className={`${input} w-40`} value={sleeve} onChange={(e) => setSleeve(e.target.value)} aria-label="sleeve">
              <option value="">— none (outside) —</option>
              {sleeves.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </label>
          <label className="flex flex-1 flex-col gap-1">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">Note</span>
            <input className={input} value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="optional, e.g. long-term hold" />
          </label>
          <Button kind="primary" disabled={!valid || save.isPending}
            onClick={() => save.mutate({ [sym.trim()]: { qty: n, cost_basis: cost.trim() ? c : null, note: note.trim() || null, sleeve: sleeve || null } })}>
            {n === 0 && shares.trim() !== "" ? "Remove" : "Save"}
          </Button>
        </div>
        <div className="mt-2"><ErrorBox error={save.error} /></div>
      </Card>

      <Card title="Recorded here">
        {d.manual.length ? (
          <SortTable<OtherRow> rows={d.manual} rowKey={(o) => o.symbol} initialSort={{ key: "value", dir: -1 }}
            columns={[
              { key: "symbol", label: "Symbol", render: (o) => <span className="font-semibold">{o.symbol}</span> },
              { key: "sleeve", label: "Sleeve", value: (o) => o.sleeve ?? "", render: (o) => (o.sleeve ? <span>{o.sleeve}</span> : <span className="text-muted">outside</span>) },
              { key: "qty", label: "Shares", align: "right", render: (o) => qty(o.qty) },
              { key: "price", label: "Close", align: "right", render: (o) => price(o.price) },
              { key: "value", label: "Value ₹", align: "right", value: (o) => o.value ?? 0, render: (o) => rupees(o.value) },
              { key: "cost_basis", label: "Avg cost", align: "right", render: (o) => price(o.cost_basis) },
              { key: "note", label: "Note", render: (o) => <span className="text-ink-2">{o.note ?? ""}</span> },
              { key: "edit", label: "", value: () => null, render: (o) => (
                <span className="flex gap-3">
                  <button className="text-accent underline-offset-2 hover:underline" onClick={() => edit(o)}>edit</button>
                  <button className="text-bad underline-offset-2 hover:underline disabled:opacity-50" disabled={save.isPending}
                    onClick={() => { if (window.confirm(`Remove ${o.symbol} (${o.qty} shares) from other holdings?`)) save.mutate({ [o.symbol]: { qty: 0, cost_basis: null, note: null } }); }}>
                    remove
                  </button>
                </span>
              ) },
            ]} />
        ) : <Empty>Nothing recorded yet.</Empty>}
      </Card>

      {d.from_tickets.length > 0 && (
        <Card title="Extra from tickets">
          <div className="mb-2 text-[12.5px] text-muted">Shares bought beyond a ticket&apos;s quantity. Change these by editing the ticket line.</div>
          <SortTable<OtherRow> rows={d.from_tickets} rowKey={(o) => `${o.symbol}|${o.source}`} initialSort={{ key: "value", dir: -1 }}
            columns={[
              { key: "symbol", label: "Symbol", render: (o) => <span className="font-semibold">{o.symbol}</span> },
              { key: "qty", label: "Shares", align: "right", render: (o) => qty(o.qty) },
              { key: "price", label: "Close", align: "right", render: (o) => price(o.price) },
              { key: "value", label: "Value ₹", align: "right", value: (o) => o.value ?? 0, render: (o) => rupees(o.value) },
              { key: "cost_basis", label: "Fill price", align: "right", render: (o) => price(o.cost_basis) },
              { key: "source", label: "Ticket line", render: (o) => <span className="num text-muted">{o.source}</span> },
            ]} />
        </Card>
      )}
    </div>
  );
}
