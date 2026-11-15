"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { EquityChart } from "@/components/EquityChart";
import { Drawdown, MultiLine, PointsBars, sleeveColor, Weights } from "@/components/PerfCharts";
import { BenchTable, MonthlyGrid, StatFacts, StatTiles } from "@/components/PerfPanels";
import { type Column, SortTable } from "@/components/SortTable";
import { Button, Card, Empty, ErrorBox, Kpi, Loading, Signed } from "@/components/ui";
import {
  api, type BookPerf, type ClosedTrade, type FundPerf, type OpenPosition, type Performance, type PeriodPerf,
} from "@/lib/api";
import { day, dayTime, pct, rupees } from "@/lib/format";
import { pts, splitPnl, turnoverPerYear } from "@/lib/perf";

const KIND: Record<string, string> = {
  grid10: "Grid-10 value book — sells only when the grid turns down and the 22-day return is positive",
  tranche: "Weekly tranches — top-5 slices held 8 weekly checks, 10% cap, trimmed weekly",
  asset: "Single instrument, held whole; resized only at the sleeve rebalance",
};

// ------------------------------------------------------------------------------------------------- fund
function FundView({ p, period }: { p: Performance; period: PeriodPerf }) {
  const f: FundPerf = period.fund;
  const labels = Object.fromEntries(period.books.map((b) => [b.id, b.label]));
  const byId = Object.fromEntries(period.books.map((b) => [b.id, b]));
  const contrib = [...f.contribution].sort((a, b) => b.pts - a.pts);
  return (
    <div className="flex flex-col gap-4">
      <StatTiles s={f.stats} vs={f.relative[0]} />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Fund (model) vs Nifty 50" className="xl:col-span-2"
          right={<span className="text-[12px] text-muted">₹ on {rupees(p.capital)} at model start · Nifty rebased to the fund on {day(period.start)} · log scale</span>}>
          <EquityChart curve={f.curve} />
        </Card>
        <Card title="Facts">
          <StatFacts s={f.stats} />
          <div className="mt-4"><BenchTable rows={f.relative} own={f.stats} ownLabel="Fund" /></div>
        </Card>
      </div>
      <Card title="Drawdown from peak">
        <Drawdown dates={f.curve.dates} main={f.drawdown.fund} mainLabel="Fund"
          others={[{ key: "nifty", label: "Nifty 50", values: f.drawdown.nifty, color: "var(--muted)" }]} />
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Where the return came from" right={<span className="text-[12px] text-muted">points of fund return</span>}>
          <PointsBars rows={contrib.map((c, i) => ({ name: c.label, pts: c.pts, color: sleeveColor(c.id, i) }))} />
          <table className="data-table mt-3">
            <thead>
              <tr><th>Sleeve</th><th className="r">Target</th><th className="r">Now</th><th className="r">Book return</th><th className="r">Contribution</th></tr>
            </thead>
            <tbody>
              {contrib.map((c) => (
                <tr key={c.id}>
                  <td><span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ background: sleeveColor(c.id) }} />{c.label}</td>
                  <td className="r num">{pct(byId[c.id]?.target_weight, 1)}</td>
                  <td className="r num">{pct(byId[c.id]?.weight, 1)}</td>
                  <td className="r"><Signed v={byId[c.id]?.stats.total_return}>{pct(byId[c.id]?.stats.total_return, 2, true)}</Signed></td>
                  <td className="r"><Signed v={c.pts}>{pts(c.pts)}</Signed></td>
                </tr>
              ))}
              <tr>
                <td className="text-muted">Rebalance costs ({f.resets.length})</td><td /><td /><td />
                <td className="r"><Signed v={f.reset_cost_pts}>{pts(f.reset_cost_pts, 3)}</Signed></td>
              </tr>
              <tr>
                <td className="font-semibold">Fund</td><td /><td /><td />
                <td className="r font-semibold"><Signed v={f.stats.total_return}>{pct(f.stats.total_return, 2, true)}</Signed></td>
              </tr>
            </tbody>
          </table>
        </Card>
        <Card title="Sleeve weights over time"
          right={<span className="text-[12px] text-muted">{f.resets.length ? `rebalanced ${f.resets.map((r) => day(r)).join(", ")}` : "no rebalance yet"}</span>}>
          <Weights dates={f.curve.dates} weights={f.weights} labels={labels} resets={f.resets} />
        </Card>
      </div>
      <Card title="Monthly returns">
        <MonthlyGrid rows={f.monthly} main="fund" compare="nifty" compareLabel="Nifty 50" />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------- book
const openCols: Column<OpenPosition>[] = [
  { key: "symbol", label: "Symbol", render: (r) => <span className="font-semibold">{r.symbol}</span> },
  { key: "opened", label: "Since", render: (r) => day(r.opened) },
  { key: "weight", label: "Weight", align: "right", render: (r) => pct(r.weight, 1) },
  { key: "ret", label: "Return", align: "right", render: (r) => <Signed v={r.ret}>{pct(r.ret, 1, true)}</Signed> },
  { key: "pnl_pts", label: "P&L", align: "right", render: (r) => <Signed v={r.pnl_pts}>{pts(r.pnl_pts)}</Signed> },
];
const closedCols: Column<ClosedTrade>[] = [
  { key: "symbol", label: "Symbol", render: (r) => <span className="font-semibold">{r.symbol}</span> },
  { key: "opened", label: "Opened", render: (r) => day(r.opened) },
  { key: "closed", label: "Closed", render: (r) => day(r.closed) },
  { key: "days", label: "Days", align: "right", render: (r) => String(r.days) },
  { key: "ret", label: "Return", align: "right", render: (r) => <Signed v={r.ret}>{pct(r.ret, 1, true)}</Signed> },
  { key: "pnl_pts", label: "P&L", align: "right", render: (r) => <Signed v={r.pnl_pts}>{pts(r.pnl_pts)}</Signed> },
];

const SHOW = 10;

/** Ranked stocks as bars: the first 10, or every one when expanded. */
function RankedPnl({ title, rows, empty, noun }: { title: string; rows: { symbol: string; pts: number }[]; empty: string; noun: string }) {
  const [open, setOpen] = useState(false);
  const shown = open ? rows : rows.slice(0, SHOW);
  const avg = rows.length ? rows.reduce((a, r) => a + r.pts, 0) / rows.length : null;
  return (
    <Card title={`${title} (${rows.length})`}
      right={rows.length > SHOW
        ? <Button onClick={() => setOpen((x) => !x)}>{open ? `Show top ${SHOW}` : `Show all ${rows.length} ${noun}`}</Button>
        : <span className="text-[12px] text-muted">points of book return, net of costs</span>}>
      {rows.length ? (
        <>
          <PointsBars rows={shown.map((x) => ({ name: x.symbol, pts: x.pts }))}
            avg={avg === null ? undefined : { value: avg, label: `avg ${pts(avg)}` }} />
          <div className="mt-1 text-[11.5px] text-muted">
            Dashed line: average of all {rows.length} {noun}, {pts(avg)} each{open || rows.length <= SHOW ? "" : ` (top ${SHOW} shown)`}.
          </div>
        </>
      ) : <Empty>{empty}</Empty>}
    </Card>
  );
}

function Trades({ b, period }: { b: BookPerf; period: PeriodPerf }) {
  const t = b.trades!;
  const [all, setAll] = useState(false);
  const perYear = turnoverPerYear(t.turnover, b.stats.years);
  const closed = all ? t.closed : t.closed.slice(0, 25);
  const { winners, losers } = splitPnl(t.pnl_by_symbol);
  const since = period.id === "all" ? "" : ` since ${day(period.start)}`;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Closed trades" value={t.closed_count} sub={`closed${since || " since start"} · ${t.open_count} open · ${t.orders} fills`} />
        <Kpi label="Hit rate" value={pct(t.hit_rate, 0)} sub="closed positions in profit" />
        <Kpi label="Avg win / loss" value={<><span className="text-good">{pct(t.avg_win, 1, true)}</span> <span className="text-muted">/</span> <span className="text-bad">{pct(t.avg_loss, 1)}</span></>} sub="per closed position" />
        <Kpi label="Expectancy" value={pct(t.expectancy, 2, true)} tone={t.expectancy === null ? undefined : t.expectancy > 0 ? "good" : "bad"} sub="mean return per closed position" />
        <Kpi label="Avg holding" value={t.avg_days === null ? "—" : `${Math.round(t.avg_days)} days`} sub="first buy to last sell" />
        <Kpi label="Turnover" value={perYear === null ? "—" : `${perYear.toFixed(1)}× / yr`} sub={t.turnover === null ? "" : `${t.turnover.toFixed(2)}× since start, build excluded`} />
      </div>
      <div className="text-[12px] text-muted">
        Stock P&L{since || " since model start"}: value at the end − value at the start + sales − purchases, in points of
        the book{since ? " at the start of the period" : ""}; every stock adds up to the book&apos;s {pct(b.stats.total_return, 2, true)}.
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <RankedPnl key={`w-${period.id}`} title="Biggest contributors" rows={winners} empty="No winners in this period." noun="winners" />
        <RankedPnl key={`l-${period.id}`} title="Biggest detractors" rows={losers} empty="No losers in this period." noun="losers" />
      </div>
      <Card title={`Open positions (${t.open.length})`} right={<span className="text-[12px] text-muted">return since each position&apos;s first buy · P&L in points of the book{since ? " at period start" : ""}</span>}>
        <SortTable rows={t.open} columns={openCols} rowKey={(r) => r.symbol} initialSort={{ key: "weight", dir: -1 }} />
      </Card>
      <Card title={`Closed positions${since} (${t.closed.length})`}
        right={t.closed.length > 25 ? <Button onClick={() => setAll((x) => !x)}>{all ? "Show latest 25" : `Show all ${t.closed.length}`}</Button> : undefined}>
        <div className="mb-2 text-[12px] text-muted">A position runs from the first buy to the sale that leaves it flat; trims and top-ups in between belong to it.</div>
        <SortTable rows={closed} columns={closedCols} rowKey={(r) => `${r.symbol}-${r.opened}`} initialSort={{ key: "closed", dir: -1 }} />
      </Card>
    </>
  );
}

function BookView({ b, period }: { b: BookPerf; period: PeriodPerf }) {
  const index = b.relative.find((r) => r.kind === "index");
  const ew = b.relative.find((r) => r.kind === "ew");
  const lines = [
    { key: "book", label: b.label, color: sleeveColor(b.id), width: 2 },
    ...(b.curve.index ? [{ key: "index", label: b.index_name, color: "var(--muted)", dash: "4 3" }] : []),
    ...(b.curve.ew ? [{ key: "ew", label: ew?.name ?? "Equal-weight universe", color: "var(--warn)", dash: "2 3" }] : []),
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="text-[13px] text-ink-2">
        {KIND[b.kind]} · {b.kind === "asset" ? b.symbol : `universe ${b.universe}`} · weight {pct(b.weight, 1)} (target {pct(b.target_weight, 1)}) ·
        contributed <Signed v={b.contribution_pts}>{pts(b.contribution_pts)}</Signed> to the fund
      </div>
      <StatTiles s={b.stats} vs={index} />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Growth of 100" className="xl:col-span-2" right={<span className="text-[12px] text-muted">rebased to 100 on {day(period.start)}</span>}>
          <MultiLine dates={b.curve.dates} series={{ book: b.curve.book, index: b.curve.index, ew: b.curve.ew }} lines={lines} />
        </Card>
        <Card title="Against benchmarks">
          <BenchTable rows={b.relative} own={b.stats} ownLabel={b.label} />
          <div className="mt-4"><StatFacts s={b.stats} /></div>
        </Card>
      </div>
      <Card title="Drawdown from peak">
        <Drawdown dates={b.curve.dates} main={b.drawdown.book} mainLabel={b.label}
          others={b.drawdown.index ? [{ key: "index", label: b.index_name, values: b.drawdown.index, color: "var(--muted)" }] : []} />
      </Card>
      <Card title="Monthly returns">
        <MonthlyGrid rows={b.monthly} main="book" compare="index" compareLabel={b.index_name} />
      </Card>
      {b.trades && <Trades b={b} period={period} />}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------- page
export default function PerformancePage() {
  const q = useQuery({ queryKey: ["performance"], queryFn: api.performance, staleTime: 5 * 60_000 });
  const [tab, setTab] = useState("fund");
  const [pid, setPid] = useState<PeriodPerf["id"]>("all");
  useEffect(() => {        // deep link: /performance?book=value&period=ytd
    const sp = new URLSearchParams(window.location.search);
    const b = sp.get("book"), per = sp.get("period");
    if (b) setTab(b);
    if (per === "all" || per === "ytd") setPid(per);
  }, []);
  if (q.isLoading) return <Loading what="Replaying the model from its start date (about 10 seconds the first time)" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const p = q.data!;
  const period = p.periods.find((x) => x.id === pid) ?? p.periods[0];
  if (!period?.books.length) return <Empty>No model yet — set up the fund in Settings.</Empty>;
  const book = period.books.find((b) => b.id === tab);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold">Performance</h1>
          <div className="text-muted">
            Model v{p.version}, replayed from {day(p.model_start)} to the close of {day(p.as_of)} with the same rules and schedule as
            the weekly runs · model figures, not your fills · costs included · Sharpe at {p.risk_free}% risk-free
          </div>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <div role="group" aria-label="Period" className="inline-flex rounded-md border border-line bg-panel p-0.5">
            {p.periods.map((x) => (
              <button key={x.id} onClick={() => setPid(x.id)} aria-pressed={x.id === period.id}
                className={`rounded px-3 py-1 text-[13px] ${x.id === period.id ? "bg-accent-soft font-semibold text-accent" : "text-ink-2 hover:text-ink"}`}>
                {x.label}
              </button>
            ))}
          </div>
          <div className="text-[12px] text-muted">computed {dayTime(p.computed_at)}</div>
        </div>
      </div>
      {period.same_as_all && (
        <div className="rounded-md border border-line bg-panel-2 px-3 py-2 text-[12.5px] text-ink-2">
          The model started this year, so {period.label.toLowerCase()} is the same period as since model start. The two views
          separate from next January.
        </div>
      )}
      <div className="flex flex-wrap gap-1 border-b border-line">
        {[{ id: "fund", label: "Fund", r: period.fund.stats.total_return }, ...period.books.map((b) => ({ id: b.id, label: b.label, r: b.stats.total_return }))].map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`-mb-px border-b-2 px-3 py-1.5 text-[13.5px] ${t.id === tab ? "border-accent font-semibold text-accent" : "border-transparent text-ink-2 hover:text-ink"}`}>
            {t.label} <Signed v={t.r}><span className="text-[12px]">{pct(t.r, 1, true)}</span></Signed>
          </button>
        ))}
      </div>
      {book ? <BookView key={`${period.id}-${book.id}`} b={book} period={period} /> : <FundView key={period.id} p={p} period={period} />}
    </div>
  );
}
