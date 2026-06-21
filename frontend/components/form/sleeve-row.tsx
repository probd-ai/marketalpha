"use client";
import type { Sleeve, Universe } from "@/lib/types";
import { StrategyPicker } from "./strategy-picker";

const F = { padding: "5px 8px", borderRadius: 7, background: "rgba(255,255,255,.06)",
  border: "1px solid rgba(255,255,255,.12)", color: "#e7edf6", fontSize: 12 } as const;

export function SleeveRow({ sleeve, onChange, onRemove, universes }:
  { sleeve: Sleeve; onChange: (s: Sleeve) => void; onRemove: () => void;
    universes: Universe[] | undefined }) {
  const set = (patch: Partial<Sleeve>) => onChange({ ...sleeve, ...patch });
  return (
    <div className="glass" style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", padding: 10 }}>
      <input value={sleeve.label} placeholder="label" onChange={(e) => set({ label: e.target.value })} style={{ ...F, width: 110 }} />
      <StrategyPicker value={sleeve.strategy_id} onChange={(id) => set({ strategy_id: id })} />
      <select value={sleeve.universe} onChange={(e) => set({ universe: e.target.value })} style={F}>
        <option value="">— universe —</option>
        {universes?.map((u) => <option key={u.value} value={u.value}>{u.value}</option>)}
      </select>
      <select value={sleeve.rebalance_frequency} onChange={(e) => set({ rebalance_frequency: e.target.value })} style={F}>
        {["monthly", "weekly", "quarterly"].map((f) => <option key={f} value={f}>{f}</option>)}
      </select>
      <select value={sleeve.rebalance_type} onChange={(e) => set({ rebalance_type: e.target.value })} style={F}>
        {["equal_weight", "skewed"].map((t) => <option key={t} value={t}>{t}</option>)}
      </select>
      <select value={sleeve.momentum_ranking} onChange={(e) => set({ momentum_ranking: e.target.value })} style={F}>
        {["20_day_return", "price_roc_66d", "price_roc_222d", "risk_adjusted", "truevx_roc",
          "short_mean_roc", "mid_mean_roc", "long_mean_roc", "stock_score_roc"].map((r) =>
          <option key={r} value={r}>{r}</option>)}
      </select>
      <input type="number" value={sleeve.max_holdings} title="max holdings" onChange={(e) => set({ max_holdings: Number(e.target.value) })} style={{ ...F, width: 60 }} />
      <input type="number" value={sleeve.weight} title="weight %" onChange={(e) => set({ weight: Number(e.target.value) })} style={{ ...F, width: 60 }} />
      <span style={{ color: "var(--color-muted)" }}>%</span>
      <button onClick={onRemove} style={{ ...F, marginLeft: "auto", color: "var(--color-neg)" }}>✕</button>
    </div>
  );
}
