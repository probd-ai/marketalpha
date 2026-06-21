"use client";
import { useState } from "react";
import type { Portfolio, Sleeve } from "@/lib/types";
import { useUniverses, useStrategies, useCreatePortfolio, useUpdatePortfolio } from "@/lib/queries";
import { SleeveRow } from "./sleeve-row";
import { GlassPanel } from "@/components/ui/glass";
import { validatePortfolioForm, sleeveWeightSum } from "@/lib/validation";
import { defaultInception, EARLIEST_INCEPTION } from "@/lib/format";
import { ApiError } from "@/lib/api";

let SEQ = 0;
const newSleeve = (weight: number): Sleeve => ({
  id: `sleeve_${Date.now()}_${SEQ++}`, label: "Sleeve", strategy_id: "", weight, universe: "",
  rebalance_frequency: "monthly", rebalance_date: "first", rebalance_type: "equal_weight",
  max_holdings: 10, momentum_ranking: "20_day_return", include_brokerage: true, exchange: "NSE",
});

const F = { padding: "6px 9px", borderRadius: 8, background: "rgba(255,255,255,.06)",
  border: "1px solid rgba(255,255,255,.12)", color: "#e7edf6", fontSize: 13 } as const;

export function PortfolioForm({ initial, onSaved }: { initial?: Portfolio; onSaved: (id: string) => void }) {
  const { data: universes } = useUniverses();
  const { data: strategies } = useStrategies();
  const create = useCreatePortfolio();
  const update = useUpdatePortfolio(initial?.id ?? "");

  const [form, setForm] = useState<Portfolio>(initial ?? {
    name: "", description: "", total_capital: 1000000, benchmark_symbol: "Nifty 50",
    inception_date: defaultInception(), point_in_time_membership: true, sleeves: [newSleeve(100)],
  });
  const [errors, setErrors] = useState<string[]>([]);
  const [apiError, setApiError] = useState<string | null>(null);
  const sum = sleeveWeightSum(form.sleeves);

  const setSleeve = (i: number, s: Sleeve) =>
    setForm({ ...form, sleeves: form.sleeves.map((x, j) => (j === i ? s : x)) });

  async function save() {
    const errs = validatePortfolioForm(form);
    setErrors(errs);
    if (errs.length) return;
    try {
      const res = initial?.id ? await update.mutateAsync(form) : await create.mutateAsync(form);
      onSaved((res as { portfolio_id: string }).portfolio_id ?? initial!.id!);
    } catch (e) {
      setApiError(e instanceof ApiError ? e.detail : "Save failed");
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 900 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>{initial ? "Edit portfolio" : "New portfolio"}</h1>
      <GlassPanel>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <input value={form.name} placeholder="Portfolio name" onChange={(e) => setForm({ ...form, name: e.target.value })} style={{ ...F, flex: 1, minWidth: 200 }} />
          <input type="number" value={form.total_capital} title="total capital ₹" onChange={(e) => setForm({ ...form, total_capital: Number(e.target.value) })} style={{ ...F, width: 140 }} />
          <input type="date" value={form.inception_date} min={EARLIEST_INCEPTION} onChange={(e) => setForm({ ...form, inception_date: e.target.value })} style={F} />
          <label style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--color-muted)", fontSize: 12 }}>
            <input type="checkbox" checked={form.point_in_time_membership} onChange={(e) => setForm({ ...form, point_in_time_membership: e.target.checked })} /> PIT
          </label>
        </div>
      </GlassPanel>

      {form.sleeves.map((s, i) => (
        <SleeveRow key={s.id} sleeve={s} universes={universes} strategies={strategies}
          onChange={(ns) => setSleeve(i, ns)}
          onRemove={() => setForm({ ...form, sleeves: form.sleeves.filter((_, j) => j !== i) })} />
      ))}

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button onClick={() => setForm({ ...form, sleeves: [...form.sleeves, newSleeve(100)] })} style={F}>+ Add sleeve</button>
        <span className="num" style={{ color: Math.abs(sum - 100) > 0.5 ? "var(--color-neg)" : "var(--color-pos)" }}>
          weights = {sum}%
        </span>
        <span style={{ flex: 1 }} />
        <button onClick={save} disabled={create.isPending || update.isPending}
          style={{ ...F, color: "#dce6ff", background: "rgba(124,156,255,.20)", border: "1px solid rgba(124,156,255,.34)" }}>
          {create.isPending || update.isPending ? "Saving…" : "Save"}
        </button>
      </div>

      {errors.length > 0 && (
        <GlassPanel><ul style={{ color: "var(--color-neg)", margin: 0, paddingLeft: 18 }}>
          {errors.map((e, i) => <li key={`${i}-${e}`}>{e}</li>)}
        </ul></GlassPanel>
      )}
      {apiError && <GlassPanel><span style={{ color: "var(--color-neg)" }}>{apiError}</span></GlassPanel>}
    </div>
  );
}
