import type { SleeveAttribution } from "@/lib/types";
import { GlassPanel, PanelTitle } from "@/components/ui/glass";
import { formatPct } from "@/lib/format";

export function SleeveAttributionPanel({ sleeves }: { sleeves: SleeveAttribution[] }) {
  const worst = sleeves.reduce<{ covered: number; total: number; missing: number }>((acc, s) => {
    if (!s.coverage) return acc;
    return { covered: acc.covered + s.coverage.covered, total: acc.total + s.coverage.total_ever_members,
      missing: acc.missing + s.coverage.missing.length };
  }, { covered: 0, total: 0, missing: 0 });
  const pct = worst.total ? Math.round(worst.covered / worst.total * 100) : null;
  return (
    <GlassPanel>
      <PanelTitle>Sleeve attribution</PanelTitle>
      <table className="num" style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
        <tbody>
          {sleeves.map((s) => (
            <tr key={s.id}>
              <td style={{ padding: "5px 0", fontWeight: 600 }}>{s.label}</td>
              <td style={{ textAlign: "right", color: "var(--color-muted)" }}>{s.weight}%</td>
              <td style={{ textAlign: "right", color: s.total_return >= 0 ? "var(--color-pos)" : "var(--color-neg)" }}>{formatPct(s.total_return)}</td>
              <td style={{ textAlign: "right", color: s.contribution >= 0 ? "var(--color-pos)" : "var(--color-neg)" }}>{formatPct(s.contribution)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {pct !== null && (
        <div style={{ fontSize: 10, color: "var(--color-muted)", marginTop: 8 }}>
          PIT coverage {pct}% · {worst.missing} ever-members unpriced
        </div>
      )}
    </GlassPanel>
  );
}
