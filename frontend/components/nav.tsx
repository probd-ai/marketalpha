"use client";
import Link from "next/link";
import { usePortfolios } from "@/lib/queries";

export function Nav() {
  const { data: portfolios } = usePortfolios();
  const item = (label: string, href?: string, active = false) => href ? (
    <Link href={href} style={{ textDecoration: "none", color: active ? "#dce6ff" : "#9fb0d4",
      fontWeight: active ? 700 : 400, padding: "5px 8px", borderRadius: 6, display: "block",
      background: active ? "rgba(124,156,255,.18)" : "transparent" }}>{label}</Link>
  ) : (
    <span style={{ color: "#5a6b7b", padding: "5px 8px", display: "block" }}>{label}</span>
  );
  return (
    <nav className="glass" style={{ width: 180, margin: 12, padding: 12, height: "fit-content",
      display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontWeight: 700, fontSize: 16, padding: "4px 8px 10px" }}>tvagent</div>
      {item("Portfolios", "/", true)}
      {item("Research")}
      {item("Backtest")}
      <div style={{ fontSize: 9, letterSpacing: ".08em", textTransform: "uppercase",
        color: "var(--color-muted)", margin: "12px 8px 4px" }}>Your books</div>
      {portfolios?.map((p) => <div key={p.id}>{item(p.name, `/portfolio/${p.id}`)}</div>)}
    </nav>
  );
}
