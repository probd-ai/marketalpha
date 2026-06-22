"use client";
import { use, useState } from "react";
import { usePortfolio, useRunPortfolio } from "@/lib/queries";
import { CommandBar, type RangeKey } from "@/components/detail/command-bar";
import { KpiStrip } from "@/components/detail/kpi-strip";
import { PerfCurve } from "@/components/detail/perf-curve";
import { GlassPanel } from "@/components/ui/glass";
import { ApiError } from "@/lib/api";
import { HoldingsTable } from "@/components/detail/holdings-table";
import { SleeveAttributionPanel } from "@/components/detail/sleeve-attribution";
import { RebalancePanel } from "@/components/detail/rebalance-panel";
import { AllocationBar } from "@/components/detail/allocation-bar";
import { defaultInception, EARLIEST_INCEPTION } from "@/lib/format";

export default function PortfolioDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [range, setRange] = useState<RangeKey>("1Y");
  const [customStart, setCustomStart] = useState(defaultInception());

  const startDate =
    range === "1Y" ? defaultInception() :
    range === "SINCE_2022" ? EARLIEST_INCEPTION :
    customStart;

  const { data: portfolio } = usePortfolio(id);
  const { data: run, isLoading, error, refetch } = useRunPortfolio(id, undefined, startDate);

  return (
    <div>
      <CommandBar name={portfolio?.name ?? "Portfolio"} asOf={run?.portfolio.as_of ?? "—"}
        range={range} onRange={setRange} onRun={() => refetch()} editHref={`/portfolio/${id}/edit`}
        customStart={customStart} onCustomStart={setCustomStart} />

      {isLoading && <GlassPanel><span style={{ color: "var(--color-muted)" }}>Running sleeves over the selected window — this can take up to a minute for long windows.</span></GlassPanel>}
      {error && (
        <GlassPanel>
          <div style={{ color: "var(--color-neg)", fontWeight: 600 }}>Run failed</div>
          <div style={{ color: "var(--color-muted)", marginTop: 6 }}>
            {error instanceof ApiError ? error.detail : "Unexpected error."}
          </div>
        </GlassPanel>
      )}

      {run && (
        <div style={{ display: "grid", gridTemplateColumns: "2.1fr 1fr", gap: 12 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <KpiStrip summary={run.summary} totalCapital={portfolio?.total_capital ?? run.summary.total_value} />
            <GlassPanel>
              <div style={{ fontSize: 9, letterSpacing: ".10em", textTransform: "uppercase",
                color: "var(--color-muted)", fontWeight: 600, marginBottom: 8 }}>
                Portfolio ₹ vs Nifty 50 · since {run.portfolio.inception_date}
              </div>
              <PerfCurve curve={run.curve} />
            </GlassPanel>
            <HoldingsTable book={run.current_book} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <SleeveAttributionPanel sleeves={run.sleeves} />
            <RebalancePanel actions={run.rebalance_actions} />
            <AllocationBar sleeves={run.sleeves} totalCapital={portfolio?.total_capital ?? run.summary.total_value} />
          </div>
        </div>
      )}
    </div>
  );
}
