import type { SleeveAttribution } from "@/lib/types";
import { GlassPanel, PanelTitle } from "@/components/ui/glass";
import { formatLakh } from "@/lib/format";

const COLORS = ["var(--color-sleeve-1)", "var(--color-sleeve-2)", "var(--color-sleeve-3)",
  "var(--color-sleeve-4)", "var(--color-sleeve-5)"];

export function AllocationBar({ sleeves, totalCapital }: { sleeves: SleeveAttribution[]; totalCapital: number }) {
  return (
    <GlassPanel>
      <PanelTitle>Capital allocation</PanelTitle>
      <div style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden", marginBottom: 8 }}>
        {sleeves.map((s, i) => <div key={s.id} style={{ width: `${s.weight}%`, background: COLORS[i % COLORS.length] }} />)}
      </div>
      <div className="num" style={{ fontSize: 10, color: "var(--color-muted)" }}>
        {sleeves.map((s) => `${s.label} ${s.weight}%`).join(" · ")} = {formatLakh(totalCapital)}
      </div>
    </GlassPanel>
  );
}
