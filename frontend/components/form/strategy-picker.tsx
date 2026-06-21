"use client";
import { useState } from "react";
import { useStrategies, useCreateStrategy } from "@/lib/queries";

const SELECT = { padding: "5px 8px", borderRadius: 7, background: "rgba(255,255,255,.06)",
  border: "1px solid rgba(255,255,255,.12)", color: "#e7edf6", fontSize: 12 } as const;

export function StrategyPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const { data: strategies } = useStrategies();
  const createStrategy = useCreateStrategy();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("truevx_score");
  const [op, setOp] = useState(">");
  const [threshold, setThreshold] = useState(90);

  async function add() {
    const res = await createStrategy.mutateAsync({ name, description: "",
      rules: [{ id: "r1", metric, operator: op, threshold, name: `${metric} ${op} ${threshold}` }] });
    onChange(res.strategy_id);
    setAdding(false); setName("");
  }

  if (adding) {
    return (
      <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
        <input value={name} placeholder="name" onChange={(e) => setName(e.target.value)} style={{ ...SELECT, width: 90 }} />
        <select value={metric} onChange={(e) => setMetric(e.target.value)} style={SELECT}>
          {["truevx_score", "mean_short", "mean_mid", "mean_long"].map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={op} onChange={(e) => setOp(e.target.value)} style={SELECT}>
          {[">", "<", ">=", "<="].map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <input type="number" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} style={{ ...SELECT, width: 60 }} />
        <button onClick={add} disabled={!name} style={SELECT}>✓</button>
      </span>
    );
  }
  return (
    <span style={{ display: "inline-flex", gap: 4 }}>
      <select value={value} onChange={(e) => onChange(e.target.value)} style={SELECT}>
        <option value="">— strategy —</option>
        {strategies?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
      <button onClick={() => setAdding(true)} style={SELECT}>＋ new</button>
    </span>
  );
}
