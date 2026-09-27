"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Button, Card, ErrorBox, Loading } from "@/components/ui";
import { api, type FundConfig } from "@/lib/api";
import { isMaterialChange } from "@/lib/config";
import { dayTime, rupees } from "@/lib/format";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">{label}</span>
      {children}
      {hint && <span className="text-[12px] text-muted">{hint}</span>}
    </label>
  );
}

const input = "rounded border border-line bg-panel px-2 py-1.5 num";

export default function Settings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["config"], queryFn: api.config });
  const [c, setC] = useState<FundConfig | null>(null);
  const [confirm, setConfirm] = useState(false);
  useEffect(() => { if (q.data) setC(structuredClone(q.data)); }, [q.data]);
  const save = useMutation({
    mutationFn: (cfg: FundConfig) => api.saveConfig({
      name: cfg.name, capital: cfg.capital, model_start: cfg.model_start, backfill_weekday: cfg.backfill_weekday,
      slippage_bp: cfg.slippage_bp, reset_cost: cfg.reset_cost, limit_band: cfg.limit_band, sleeves: cfg.sleeves,
      rebalance_months: cfg.rebalance_months, rebalance_band: cfg.rebalance_band,
    }),
    onSuccess: () => { setConfirm(false); qc.invalidateQueries(); },
  });
  if (q.isLoading || !c) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const orig = q.data!;
  const today = new Date().toISOString().slice(0, 10);
  const wsum = c.sleeves.reduce((a, s) => a + s.weight, 0);
  const material = isMaterialChange(orig, c);
  const dirty = JSON.stringify(orig) !== JSON.stringify({ ...orig, ...c });
  const upd = (patch: Partial<FundConfig>) => setC({ ...c, ...patch });

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="text-[20px] font-semibold">Settings</h1>
        <div className="text-muted">Model version {orig.version} · last saved {dayTime(orig.updated_at)}</div>
      </div>
      <Card title="Fund">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="Name"><input className={input} value={c.name} onChange={(e) => upd({ name: e.target.value })} /></Field>
          <Field label="Capital (₹)" hint={rupees(c.capital)}>
            <input className={input} type="number" min={1} step={10000} value={c.capital} onChange={(e) => upd({ capital: Number(e.target.value) })} />
          </Field>
          <Field label="Model start" hint="From 2020-10-01 (earliest validated point-in-time data) to today. An early date replays to a mature book; today builds the books from cash.">
            <input className={input} type="date" min="2020-10-01" max={today} value={c.model_start} onChange={(e) => upd({ model_start: e.target.value })} />
          </Field>
          <Field label="History weekday" hint="Weekday the model assumes for weeks before your first published run. After that, your actual run days are used.">
            <select className={input} value={c.backfill_weekday} onChange={(e) => upd({ backfill_weekday: Number(e.target.value) })}>
              {WEEKDAYS.map((w, i) => <option key={w} value={i}>{w}</option>)}
            </select>
          </Field>
          <Field label="Slippage per side (bp)"><input className={input} type="number" min={0} step={1} value={c.slippage_bp} onChange={(e) => upd({ slippage_bp: Number(e.target.value) })} /></Field>
          <Field label="Limit band (±%)" hint="Limit price guidance on the ticket, around the reference close.">
            <input className={input} type="number" min={0} step={0.05} value={+(c.limit_band * 100).toFixed(3)} onChange={(e) => upd({ limit_band: Number(e.target.value) / 100 })} />
          </Field>
          <Field label="Rebalance cost (%)"><input className={input} type="number" min={0} step={0.01} value={+(c.reset_cost * 100).toFixed(3)} onChange={(e) => upd({ reset_cost: Number(e.target.value) / 100 })} /></Field>
        </div>
      </Card>
      <Card title="Fund rebalance (money between sleeves)">
        <div className="mb-3 text-[12.5px] text-ink-2">
          Restores the sleeves to their target weights on the first run day of the chosen months. The drift band adds a safety net: on any
          month&apos;s first run day, rebalance early if a sleeve is further than the band from its target. Tested: half-yearly (Jan/Jul) + 5-pt band
          gives the same return as monthly with 2 heavy Mondays a year instead of 12.
        </div>
        <div className="flex flex-wrap gap-1.5">
          {["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].map((m, i) => {
            const on = c.rebalance_months.includes(i + 1);
            return (
              <button key={m} type="button" aria-pressed={on}
                onClick={() => upd({ rebalance_months: on ? c.rebalance_months.filter((x) => x !== i + 1) : [...c.rebalance_months, i + 1].sort((a, b) => a - b) })}
                className={`rounded border px-2.5 py-1 text-[12.5px] ${on ? "border-accent bg-accent-soft font-semibold text-accent" : "border-line text-ink-2 hover:bg-panel-2"}`}>{m}</button>
            );
          })}
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-4">
          <Field label="Drift band (points)" hint="Rebalance early when any sleeve is more than this far from target. Leave empty to switch off.">
            <input className={`${input} w-28`} type="number" min={0.5} max={50} step={0.5}
              value={c.rebalance_band === null ? "" : +(c.rebalance_band * 100).toFixed(2)}
              onChange={(e) => upd({ rebalance_band: e.target.value === "" ? null : Number(e.target.value) / 100 })} />
          </Field>
        </div>
        {c.rebalance_months.length === 0 && <div className="mt-2 text-bad">Choose at least one month.</div>}
      </Card>
      <Card title={`Sleeves · weights total ${(wsum * 100).toFixed(1)}%`}>
        <table className="grid">
          <thead><tr><th>Sleeve</th><th>Rule set</th><th>Universe / instrument</th><th>Parameters</th><th className="r">Weight %</th><th className="r">Capital</th></tr></thead>
          <tbody>
            {c.sleeves.map((s, i) => (
              <tr key={s.id}>
                <td className="font-medium">{s.label}</td>
                <td>{s.kind === "grid10" ? "Grid-10" : s.kind === "tranche" ? "Weekly tranche" : "Asset (ETF)"}</td>
                <td>{s.universe ?? s.symbol}</td>
                <td className="text-[12px] text-muted">{s.params ? Object.entries(s.params).map(([k, v]) => `${k}=${v}`).join(" · ") : "—"}</td>
                <td className="r">
                  <input aria-label={`weight ${s.label}`} className={`${input} w-20 text-right`} type="number" min={0} max={100} step={0.5}
                    value={+(s.weight * 100).toFixed(2)}
                    onChange={(e) => upd({ sleeves: c.sleeves.map((x, j) => (j === i ? { ...x, weight: Number(e.target.value) / 100 } : x)) })} />
                </td>
                <td className="r num">{rupees(s.weight * c.capital)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {Math.abs(wsum - 1) > 1e-6 && <div className="mt-2 text-bad">Weights must total 100%.</div>}
      </Card>
      {!confirm ? (
        <div className="flex gap-2">
          <Button kind="primary" disabled={!dirty || Math.abs(wsum - 1) > 1e-6 || c.rebalance_months.length === 0} onClick={() => (material ? setConfirm(true) : save.mutate(c))}>Save settings</Button>
          <Button disabled={!dirty} onClick={() => setC(structuredClone(orig))}>Revert</Button>
        </div>
      ) : (
        <div className="rounded-lg border border-warn bg-warn-soft p-4">
          <div className="font-semibold">This starts model version {orig.version + 1}</div>
          <div className="mt-1 text-ink-2">
            Capital, model start, sleeves or cost settings changed. The model is replayed from its start under the new settings; runs
            published under version {orig.version} stay in the journal unchanged but no longer steer the model.
          </div>
          <div className="mt-3 flex gap-2">
            <Button kind="primary" onClick={() => save.mutate(c)} disabled={save.isPending}>Confirm and save</Button>
            <Button onClick={() => setConfirm(false)}>Cancel</Button>
          </div>
        </div>
      )}
      <ErrorBox error={save.error} />
    </div>
  );
}
