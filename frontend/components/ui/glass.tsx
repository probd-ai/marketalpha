import type { ReactNode } from "react";

type Tone = "pos" | "neg" | "muted" | "warn" | "default";
const toneColor: Record<Tone, string> = {
  pos: "var(--color-pos)", neg: "var(--color-neg)", muted: "var(--color-muted)",
  warn: "#f3c277", default: "#e7edf6",
};

export function GlassPanel({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return <div className="glass" style={{ padding: "13px 15px", ...style }}>{children}</div>;
}

export function PanelTitle({ children }: { children: ReactNode }) {
  return <div style={{ fontSize: 9, letterSpacing: ".10em", textTransform: "uppercase",
    color: "var(--color-muted)", fontWeight: 600, marginBottom: 8 }}>{children}</div>;
}

export function Badge({ children, tone = "muted" }: { children: ReactNode; tone?: Tone }) {
  const warn = tone === "warn";
  return <span style={{ fontSize: 9, padding: "3px 9px", borderRadius: 999, letterSpacing: ".04em",
    color: toneColor[tone], background: warn ? "rgba(245,180,90,.14)" : "rgba(255,255,255,.06)",
    border: `1px solid ${warn ? "rgba(245,180,90,.28)" : "rgba(255,255,255,.10)"}` }}>{children}</span>;
}

export function Stat({ label, value, sub, tone = "default" }:
  { label: string; value: string; sub?: string; tone?: Tone }) {
  return (
    <div style={{ padding: "12px 14px" }}>
      <div style={{ fontSize: 9, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--color-muted)" }}>{label}</div>
      <div className="num" style={{ fontSize: 18, fontWeight: 700, marginTop: 3, color: toneColor[tone] }}>{value}</div>
      {sub && <div className="num" style={{ fontSize: 10, color: "var(--color-muted)" }}>{sub}</div>}
    </div>
  );
}
