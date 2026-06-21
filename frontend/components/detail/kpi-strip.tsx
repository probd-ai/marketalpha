import type { RunSummary } from "@/lib/types";
import { Stat } from "@/components/ui/glass";
import { formatLakh, formatPct } from "@/lib/format";

export function KpiStrip({ summary, totalCapital }: { summary: RunSummary; totalCapital: number }) {
  return (
    <div className="glass" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", padding: 0, overflow: "hidden" }}>
      <Stat label="Invested → Now" value={formatLakh(summary.total_value)} sub={formatPct(summary.total_return)} tone={summary.total_return >= 0 ? "pos" : "neg"} />
      <Stat label="Nifty 50 (same ₹)" value={formatLakh(totalCapital * (1 + summary.benchmark_return / 100))} sub={formatPct(summary.benchmark_return)} tone="muted" />
      <Stat label="Alpha vs Nifty 50" value={formatPct(summary.alpha)} tone={summary.alpha >= 0 ? "pos" : "neg"} />
      <Stat label="Sharpe · MaxDD" value={summary.sharpe_ratio.toFixed(2)} sub={formatPct(summary.max_drawdown, false)} tone="default" />
    </div>
  );
}
