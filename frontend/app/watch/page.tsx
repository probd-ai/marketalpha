"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { SortTable } from "@/components/SortTable";
import { Badge, Card, Empty, ErrorBox, Kpi, Loading } from "@/components/ui";
import { api, type WatchHolding, type WatchPick } from "@/lib/api";
import { day, qty } from "@/lib/format";

const pct = (v: number | null) => (v === null || v === undefined ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);
const num = (v: number | null) => (v === null || v === undefined ? "—" : v.toFixed(1));

/** Daily overlay on Grid-10: exit a holding once mean_long drops below its 222-session SMA; refill from Grid-10's
 *  next picks that are above it. Research: Grid-10 + this exit ≈ 10.3–10.6%/yr vs 9.5% plain (2008 → 2026). */
export default function StreakWatch() {
  const q = useQuery({ queryKey: ["streak-watch"], queryFn: api.streakWatch });
  if (q.isLoading) return <Loading what="Computing streaks" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const w = q.data!;
  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <div>
        <h1 className="text-[20px] font-semibold">Streak watch</h1>
        <div className="text-muted">
          Daily check on the Grid-10 sleeves, as of the close of {w.as_of ? day(w.as_of) : "—"}. A holding whose mean_long is <b>below</b> its
          {" "}{w.sma}-session average is an <b>exit</b>: sell at the next open and replace it with Grid-10&apos;s first pick that is above its average.
          Record both on <Link href="/tracking" className="text-accent hover:underline">Tracking → manual trades</Link>.
        </div>
      </div>
      {w.sleeves.length === 0 && <Empty>No Grid-10 sleeve in this fund.</Empty>}
      {w.sleeves.map((sl) => {
        const exits = sl.holdings.filter((h) => h.exit);
        const firstPick = sl.picks.find((p) => p.eligible);
        return (
          <div key={sl.sleeve} className="flex flex-col gap-3">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <Kpi label={`${sl.sleeve} holdings`} value={sl.holdings.length} sub={sl.universe} />
              <Kpi label="Exit now" value={exits.length ? exits.map((h) => h.symbol).join(", ") : "none"} tone={exits.length ? "bad" : "good"} />
              <Kpi label="Next Grid-10 pick" value={firstPick?.symbol ?? "—"} sub={firstPick ? `22-day ${pct(firstPick.roc22)}, above its average` : undefined} />
            </div>
            <Card title={`${sl.sleeve} — holdings`}>
              {sl.holdings.length ? (
                <SortTable<WatchHolding> rows={sl.holdings} rowKey={(h) => h.symbol} initialSort={{ key: "gap", dir: 1 }}
                  columns={[
                    { key: "symbol", label: "Symbol", render: (h) => <span className="font-semibold">{h.symbol}</span> },
                    { key: "qty", label: "Shares", align: "right", render: (h) => qty(h.qty) },
                    { key: "grid", label: "Grid", render: (h) => (h.grid === 1 ? "UP" : h.grid === -1 ? "DOWN" : "—") },
                    { key: "roc22", label: "22-day", align: "right", render: (h) => pct(h.roc22) },
                    { key: "mean_long", label: "mean_long", align: "right", render: (h) => num(h.mean_long) },
                    { key: "sma", label: "222 avg", align: "right", render: (h) => num(h.sma) },
                    { key: "gap", label: "Gap", align: "right", value: (h) => (h.mean_long ?? 0) - (h.sma ?? 0),
                      render: (h) => (h.mean_long !== null && h.sma !== null ? `${h.mean_long - h.sma >= 0 ? "+" : ""}${(h.mean_long - h.sma).toFixed(1)}` : "—") },
                    { key: "days", label: "Streak", render: (h) => (h.above === null ? "—" : `${h.above ? "above" : "below"} ${h.days}d`) },
                    { key: "exit", label: "", value: (h) => (h.exit ? 0 : 1),
                      render: (h) => (h.exit ? <Badge value="BLOCK" label="exit" /> : !h.in_index ? <Badge value="WARN" label="left index" /> : null) },
                  ]} />
              ) : <Empty>Nothing held in this sleeve yet.</Empty>}
            </Card>
            <Card title="Grid-10 next picks (grid UP, lowest 22-day return first)">
              <SortTable<WatchPick> rows={sl.picks} rowKey={(p) => p.symbol} initialSort={{ key: "roc22", dir: 1 }}
                columns={[
                  { key: "symbol", label: "Symbol", render: (p) => <span className={p.eligible ? "font-semibold" : "text-muted line-through"}>{p.symbol}</span> },
                  { key: "roc22", label: "22-day", align: "right", render: (p) => pct(p.roc22) },
                  { key: "eligible", label: "Streak", value: (p) => (p.eligible ? 0 : 1),
                    render: (p) => (p.eligible ? <span className="text-good">above — can buy</span> : <span className="text-muted">below its average — skip</span>) },
                ]} />
            </Card>
          </div>
        );
      })}
    </div>
  );
}
