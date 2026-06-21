"use client";
import { useState } from "react";
import type { Holding } from "@/lib/types";
import { PanelTitle, GlassPanel } from "@/components/ui/glass";
import { formatINR, formatPct } from "@/lib/format";

export function HoldingsTable({ book }: { book: Holding[] }) {
  const [desc, setDesc] = useState(true);
  const rows = [...book].sort((a, b) => (desc ? b.market_value - a.market_value : a.market_value - b.market_value));
  const th = { textAlign: "right" as const, fontSize: 8.5, letterSpacing: ".06em", textTransform: "uppercase" as const, color: "#7d8cae", padding: "0 0 7px" };
  return (
    <GlassPanel>
      <PanelTitle>Current book · {book.length} holdings</PanelTitle>
      <table className="num" style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
        <thead><tr>
          <th style={{ ...th, textAlign: "left" }}>Symbol</th>
          <th style={th}>Sleeve</th>
          <th style={{ ...th, cursor: "pointer" }} onClick={() => setDesc(!desc)}>Wt</th>
          <th style={th}>Mkt value</th><th style={th}>P&amp;L</th>
        </tr></thead>
        <tbody>
          {rows.map((h) => (
            <tr key={h.symbol}>
              <td style={{ padding: "6px 0", borderTop: "1px solid rgba(255,255,255,.055)", fontWeight: 600 }}>{h.symbol}</td>
              <td style={{ textAlign: "right", color: "var(--color-muted)", borderTop: "1px solid rgba(255,255,255,.055)" }}>{h.sleeve}</td>
              <td style={{ textAlign: "right", borderTop: "1px solid rgba(255,255,255,.055)" }}>{h.weight.toFixed(1)}%</td>
              <td style={{ textAlign: "right", borderTop: "1px solid rgba(255,255,255,.055)" }}>{formatINR(h.market_value)}</td>
              <td style={{ textAlign: "right", borderTop: "1px solid rgba(255,255,255,.055)", color: h.pnl >= 0 ? "var(--color-pos)" : "var(--color-neg)" }}>{formatPct(h.pnl_percent)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </GlassPanel>
  );
}
