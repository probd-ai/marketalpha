import Link from "next/link";
import type { Portfolio } from "@/lib/types";
import { GlassPanel } from "@/components/ui/glass";
import { formatLakh } from "@/lib/format";

const SLEEVE_COLORS = ["var(--color-sleeve-1)", "var(--color-sleeve-2)", "var(--color-sleeve-3)",
  "var(--color-sleeve-4)", "var(--color-sleeve-5)"];

export function PortfolioCard({ portfolio }: { portfolio: Portfolio }) {
  return (
    <Link href={`/portfolio/${portfolio.id}`} style={{ textDecoration: "none", color: "inherit" }}>
      <GlassPanel style={{ minHeight: 110 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{portfolio.name}</div>
        <div style={{ display: "flex", gap: 4, margin: "10px 0" }}>
          {portfolio.sleeves.map((s, i) => (
            <span key={s.id} title={s.label} style={{ width: 9, height: 9, borderRadius: 2,
              background: SLEEVE_COLORS[i % SLEEVE_COLORS.length] }} />
          ))}
        </div>
        <div className="num" style={{ fontSize: 12, color: "var(--color-muted)" }}>
          {portfolio.sleeves.length} sleeves · {formatLakh(portfolio.total_capital)}
        </div>
      </GlassPanel>
    </Link>
  );
}
