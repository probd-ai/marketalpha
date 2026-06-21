"use client";
import Link from "next/link";
import { usePortfolios } from "@/lib/queries";
import { PortfolioCard } from "@/components/portfolio-card";
import { GlassPanel } from "@/components/ui/glass";

export default function Home() {
  const { data: portfolios, isLoading, error } = usePortfolios();
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, flex: 1 }}>Portfolios</h1>
        <Link href="/portfolio/new" style={{ textDecoration: "none" }}>
          <span style={{ fontSize: 12, padding: "7px 13px", borderRadius: 8, color: "#dce6ff",
            background: "rgba(124,156,255,.20)", border: "1px solid rgba(124,156,255,.34)" }}>+ New portfolio</span>
        </Link>
      </div>

      {isLoading && <GlassPanel><span style={{ color: "var(--color-muted)" }}>Loading…</span></GlassPanel>}
      {error && <GlassPanel><span style={{ color: "var(--color-neg)" }}>Failed to load portfolios.</span></GlassPanel>}
      {portfolios && portfolios.length === 0 && (
        <GlassPanel style={{ textAlign: "center", padding: "40px" }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>No portfolios yet</div>
          <div style={{ color: "var(--color-muted)", margin: "8px 0 16px" }}>Create your first portfolio to start.</div>
          <Link href="/portfolio/new" style={{ color: "#dce6ff" }}>+ New portfolio</Link>
        </GlassPanel>
      )}
      {portfolios && portfolios.length > 0 && (
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
          {portfolios.map((p) => <PortfolioCard key={p.id} portfolio={p} />)}
        </div>
      )}
    </div>
  );
}
