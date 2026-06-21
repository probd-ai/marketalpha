"use client";
import Link from "next/link";
import { Badge } from "@/components/ui/glass";

export type RangeKey = "1Y" | "SINCE_2022" | "CUSTOM";

export function CommandBar({ name, asOf, range, onRange, onRun, editHref }:
  { name: string; asOf: string; range: RangeKey; onRange: (r: RangeKey) => void; onRun: () => void; editHref: string }) {
  const seg = (key: RangeKey, label: string) => (
    <span onClick={() => onRange(key)} style={{ padding: "3px 9px", borderRadius: 6, cursor: "pointer",
      color: range === key ? "#dce6ff" : "#9fb0d4",
      background: range === key ? "rgba(124,156,255,.28)" : "transparent" }}>{label}</span>
  );
  return (
    <div className="glass" style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: 15, fontWeight: 700 }}>{name}</div>
      <Badge tone="warn">HYPOTHETICAL · PIT</Badge>
      <span style={{ flex: 1 }} />
      <div style={{ display: "flex", gap: 2, padding: 2, borderRadius: 8, fontSize: 10,
        background: "rgba(255,255,255,.05)", border: "1px solid rgba(255,255,255,.08)" }}>
        {seg("1Y", "1Y")}{seg("SINCE_2022", "Since 2022")}{seg("CUSTOM", "Custom")}
      </div>
      <span className="num" style={{ fontSize: 10, color: "var(--color-muted)" }}>As of {asOf}</span>
      <button onClick={onRun} style={{ fontSize: 11, padding: "5px 11px", borderRadius: 8, cursor: "pointer",
        color: "#dce6ff", background: "rgba(124,156,255,.20)", border: "1px solid rgba(124,156,255,.34)" }}>↻ Run live</button>
      <Link href={editHref} style={{ fontSize: 11, padding: "5px 11px", borderRadius: 8, color: "#cdd7ee",
        background: "rgba(255,255,255,.06)", border: "1px solid rgba(255,255,255,.12)", textDecoration: "none" }}>Edit</Link>
    </div>
  );
}
