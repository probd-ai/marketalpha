"use client";

import type { ReactNode } from "react";

import { Kpi, Signed } from "@/components/ui";
import type { MonthlyRow, PerfStats, Relative } from "@/lib/api";
import { day, pct } from "@/lib/format";
import { heat, MONTHS, monthGrid, monthLabel, ratio, type SeriesKey } from "@/lib/perf";

const tone = (v: number | null | undefined) => (v === null || v === undefined ? undefined : v > 0 ? "good" : v < 0 ? "bad" : undefined);

/** Headline figures for any curve. `vsLabel`/`vs` adds the main benchmark's return under the total. */
export function StatTiles({ s, vs }: { s: PerfStats; vs?: Relative }) {
  const dd = s.max_dd_recovered ? `recovered ${day(s.max_dd_recovered)}` : s.max_dd < 0 ? "not yet recovered" : "";
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Kpi label="Total return" value={pct(s.total_return, 2, true)} tone={tone(s.total_return)}
        sub={vs ? `${vs.name} ${pct(vs.total_return, 2, true)}` : `${day(s.start)} → ${day(s.end)}`} />
      <Kpi label="CAGR" value={s.cagr === null ? "—" : pct(s.cagr, 1, true)} tone={tone(s.cagr)}
        sub={s.cagr === null ? `needs a full year (${s.years.toFixed(2)} so far)` : `${s.years.toFixed(1)} years`} />
      <Kpi label="Volatility" value={pct(s.vol, 1)} sub="annualised, daily returns" />
      <Kpi label="Sharpe" value={ratio(s.sharpe)} sub={`Sortino ${ratio(s.sortino)} · rf 0%`} />
      <Kpi label="Max drawdown" value={pct(s.max_dd, 1)} tone={s.max_dd < 0 ? "bad" : undefined}
        sub={s.max_dd < 0 ? `${day(s.max_dd_peak)} → ${day(s.max_dd_trough)} · ${dd}` : "none"} />
      <Kpi label="From peak now" value={pct(s.dd_now, 1)} tone={s.dd_now < 0 ? "bad" : "good"}
        sub={s.dd_now === 0 ? "at a high" : "below its high"} />
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line py-1.5 last:border-0">
      <span className="text-muted">{label}</span>
      <span className="num text-right">{children}</span>
    </div>
  );
}

/** Secondary facts: best/worst day and month, hit rate of months. */
export function StatFacts({ s }: { s: PerfStats }) {
  return (
    <div className="text-[13px]">
      <Fact label="Best month">{s.best_month ? <><Signed v={s.best_month.r}>{pct(s.best_month.r, 2, true)}</Signed> <span className="text-muted">{monthLabel(s.best_month.m)}</span></> : "—"}</Fact>
      <Fact label="Worst month">{s.worst_month ? <><Signed v={s.worst_month.r}>{pct(s.worst_month.r, 2, true)}</Signed> <span className="text-muted">{monthLabel(s.worst_month.m)}</span></> : "—"}</Fact>
      <Fact label="Months positive">{s.pct_months_positive === null ? "—" : `${pct(s.pct_months_positive, 0)} of ${s.months}`}</Fact>
      <Fact label="Best day">{s.best_day ? <><Signed v={s.best_day.r}>{pct(s.best_day.r, 2, true)}</Signed> <span className="text-muted">{day(s.best_day.date)}</span></> : "—"}</Fact>
      <Fact label="Worst day">{s.worst_day ? <><Signed v={s.worst_day.r}>{pct(s.worst_day.r, 2, true)}</Signed> <span className="text-muted">{day(s.worst_day.date)}</span></> : "—"}</Fact>
      <Fact label="Sessions">{s.sessions}</Fact>
    </div>
  );
}

/** The curve against each benchmark over the same dates. */
export function BenchTable({ rows, own, ownLabel }: { rows: Relative[]; own: PerfStats; ownLabel: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr><th>Series</th><th className="r">Return</th><th className="r">Excess</th><th className="r">Beta</th><th className="r">Correlation</th></tr>
        </thead>
        <tbody>
          <tr>
            <td className="font-semibold">{ownLabel}</td>
            <td className="r"><Signed v={own.total_return}>{pct(own.total_return, 2, true)}</Signed></td>
            <td className="r text-muted">—</td><td className="r text-muted">—</td><td className="r text-muted">—</td>
          </tr>
          {rows.map((r) => (
            <tr key={r.name}>
              <td>{r.name} <span className="text-[11px] text-muted">{r.kind === "ew" ? "equal-weight, point-in-time members" : "index"}</span></td>
              <td className="r"><Signed v={r.total_return}>{pct(r.total_return, 2, true)}</Signed></td>
              <td className="r"><Signed v={r.excess}>{pct(r.excess, 2, true)}</Signed></td>
              <td className="r num">{ratio(r.beta)}</td>
              <td className="r num">{ratio(r.corr)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Calendar of monthly returns, newest year first; the comparison series in small type under each cell. */
export function MonthlyGrid({ rows, main, compare, compareLabel }: {
  rows: MonthlyRow[]; main: SeriesKey; compare?: SeriesKey; compareLabel?: string;
}) {
  const grid = monthGrid(rows, main, compare);
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Year</th>
            {MONTHS.map((m) => <th key={m} className="r">{m}</th>)}
            <th className="r">Year</th>
          </tr>
        </thead>
        <tbody>
          {grid.map((y) => (
            <tr key={y.year}>
              <td className="font-semibold">{y.year}</td>
              {y.cells.map((v, i) => (
                <td key={i} className="r num" style={{ background: heat(v) }}>
                  {v === null ? <span className="text-muted">·</span> : pct(v, 1)}
                  {compare && y.compare[i] !== null && <div className="text-[10.5px] text-muted">{pct(y.compare[i], 1)}</div>}
                </td>
              ))}
              <td className="r num font-semibold" style={{ background: heat(y.year_return, 25) }}>
                {pct(y.year_return, 1)}
                {compare && y.compare_year !== null && <div className="text-[10.5px] font-normal text-muted">{pct(y.compare_year, 1)}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {compare && <div className="mt-2 text-[11.5px] text-muted">Small figures: {compareLabel}. A part year compounds only the months shown (year to date).</div>}
    </div>
  );
}
