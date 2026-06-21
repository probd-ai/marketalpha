import type { RunResult } from "@/lib/types";
import { GlassPanel, PanelTitle } from "@/components/ui/glass";

export function RebalancePanel({ actions }: { actions: RunResult["rebalance_actions"] }) {
  const Row = ({ op, sym, sleeve, tone }: { op: string; sym: string; sleeve: string; tone: string }) => (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 9px", borderRadius: 8,
      background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.06)" }}>
      <span style={{ fontWeight: 700, width: 14, textAlign: "center", color: tone }}>{op}</span>
      <span style={{ fontWeight: 600 }}>{sym}</span>
      <span style={{ fontSize: 10, color: "var(--color-muted)" }}>{sleeve}</span>
    </div>
  );
  return (
    <GlassPanel>
      <PanelTitle>Rebalance{actions.next_due ? ` · next due ${actions.next_due}` : ""}</PanelTitle>
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {actions.add.map((a, i) => <Row key={`a${i}`} op="+" sym={a.symbol} sleeve={a.sleeve} tone="var(--color-pos)" />)}
        {actions.exit.map((e, i) => <Row key={`e${i}`} op="−" sym={e.symbol} sleeve={e.sleeve} tone="var(--color-neg)" />)}
        {actions.add.length === 0 && actions.exit.length === 0 &&
          <span style={{ fontSize: 11, color: "var(--color-muted)" }}>No changes at the latest rebalance.</span>}
      </div>
    </GlassPanel>
  );
}
