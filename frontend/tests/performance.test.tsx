import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BookPerf, FundPerf, PeriodPerf, Performance, PerfStats } from "@/lib/api";

vi.mock("@/lib/api", async (orig) => {
  const mod = await orig<typeof import("@/lib/api")>();
  return { ...mod, api: { ...mod.api, performance: vi.fn() } };
});
import { api, ApiError } from "@/lib/api";
import PerformancePage from "@/app/performance/page";

// jsdom has no ResizeObserver; recharts' ResponsiveContainer needs one (charts render empty, tables are what we assert)
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

const stats = (p: Partial<PerfStats> = {}): PerfStats => ({
  start: "2025-12-31", end: "2026-11-13", sessions: 216, years: 0.87, total_return: 13.51, cagr: null, vol: 20.16,
  sharpe: 0.84, sortino: 1.2, max_dd: -17.13, max_dd_peak: "2026-01-29", max_dd_trough: "2026-03-23",
  max_dd_recovered: "2026-06-10", dd_now: -5.09, best_day: { date: "2026-04-15", r: 3.1 },
  worst_day: { date: "2026-03-02", r: -4.2 }, best_month: { m: "2026-05", r: 7.5 }, worst_month: { m: "2026-03", r: -9.1 },
  months: 11, pct_months_positive: 54.5, ...p,
});
const dates = ["2025-12-31", "2026-01-01", "2026-01-02"];
// 12 winners (W01 +12 … W12 +1) and 2 losers: more than the 10 shown before expanding
const PNL = [...Array.from({ length: 12 }, (_, i) => ({ symbol: `W${String(i + 1).padStart(2, "0")}`, pts: 12 - i })),
  { symbol: "L1", pts: -0.5 }, { symbol: "L2", pts: -2 }];

const book = (p: Partial<BookPerf>): BookPerf => ({
  id: "growth", label: "Growth", kind: "tranche", universe: "NIFTY200", symbol: null, index_name: "Nifty 100",
  target_weight: 29.5, weight: 31.5, contribution_pts: 6.44, stats: stats({ total_return: 21.85 }),
  relative: [{ name: "Nifty 100", kind: "index", total_return: -2.4, excess: 24.25, beta: 1.06, corr: 0.74 },
             { name: "Equal-weight NIFTY200", kind: "ew", total_return: 0.79, excess: 21.06, beta: 1.07, corr: 0.83 }],
  curve: { dates, book: [100, 101, 102], index: [100, 99, 98], ew: [100, 100, 101] },
  drawdown: { book: [0, 0, 0], index: [0, -1, -2] },
  monthly: [{ m: "2026-01", book: 2, index: -1, ew: 0.5 }],
  trades: {
    orders: 520, closed_count: 2, open_count: 1, hit_rate: 50, avg_win: 10.25, avg_loss: -9.47, expectancy: 0.39,
    avg_days: 70.6, turnover: 5.28,
    closed: [{ symbol: "AAA", opened: "2026-01-05", closed: "2026-03-02", days: 56, ret: 10.25, pnl_pts: 1.2 },
             { symbol: "BBB", opened: "2026-02-02", closed: "2026-04-06", days: 63, ret: -9.47, pnl_pts: -0.9 }],
    open: [{ symbol: "CCC", opened: "2026-06-01", weight: 9.8, ret: 30, pnl_pts: 3.4 }],
    pnl_by_symbol: PNL,
  },
  ...p,
});

const fund = (p: Partial<FundPerf> = {}): FundPerf => ({
  stats: stats(), relative: [{ name: "Nifty 50", kind: "index", total_return: -3.83, excess: 17.34, beta: 0.9, corr: 0.68 }],
  curve: { dates, fund: [5737297, 5740000, 5750000], nifty: [5737297, 5700000, 5690000] },
  drawdown: { fund: [0, 0, 0], nifty: [0, -0.6, -0.8] },
  monthly: [{ m: "2026-01", fund: 1.5, nifty: -0.7 }, { m: "2026-02", fund: -2, nifty: 1 }],
  contribution: [{ id: "growth", label: "Growth", pts: 6.44 }, { id: "gold", label: "Gold", pts: 0.09 }],
  reset_cost_pts: -0.011, resets: ["2026-07-06"],
  weights: { growth: [29.5, 29.6, 29.7], gold: [70.5, 70.4, 70.3] }, ...p,
});

const gold = book({ id: "gold", label: "Gold", kind: "asset", universe: null, symbol: "GOLDBEES", index_name: "Nifty 50",
  trades: null, relative: [{ name: "Nifty 50", kind: "index", total_return: -3.83, excess: 5.07, beta: 0.61, corr: 0.28 }],
  stats: stats({ total_return: 1.24 }), curve: { dates, book: [100, 100, 101], index: [100, 99, 98] } });

const growthTrades = book({}).trades!;
const ALL: PeriodPerf = { id: "all", label: "Since model start", start: "2025-12-31", same_as_all: false, fund: fund(), books: [book({}), gold] };
const YTD: PeriodPerf = {
  id: "ytd", label: "2027 to date", start: "2026-12-31", same_as_all: false,
  fund: fund({ stats: stats({ start: "2026-12-31", total_return: 4.2 }) }),
  books: [book({ stats: stats({ start: "2026-12-31", total_return: 7.7 }),
                 trades: { ...growthTrades, closed_count: 1, closed: [growthTrades.closed[0]] } }), gold],
};
const PERF: Performance = {
  as_of: "2027-03-12", model_start: "2026-01-01", version: 9, capital: 5737297, risk_free: 0,
  computed_at: "2027-03-14T16:00:00+00:00", periods: [ALL, YTD],
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><PerformancePage /></QueryClientProvider>);
}

describe("Performance page", () => {
  beforeEach(() => { vi.mocked(api.performance).mockResolvedValue(PERF); });
  afterEach(cleanup);

  it("opens on the fund since model start: headline, contribution, monthly grid", async () => {
    renderPage();
    expect(await screen.findByText("Fund (model) vs Nifty 50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Since model start" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByText("+13.51%").length).toBeGreaterThan(0);
    expect(screen.getByText("Nifty 50 −3.83%")).toBeInTheDocument();
    expect(screen.getByText(/needs a full year/)).toBeInTheDocument();          // CAGR never annualises a part year
    const contrib = screen.getByText("Where the return came from").closest("section")!;
    expect(within(contrib).getByText("+6.44 pts")).toBeInTheDocument();
    expect(within(contrib).getByText("Rebalance costs (1)")).toBeInTheDocument();
    const monthly = screen.getByText("Monthly returns").closest("section")!;
    expect(within(monthly).getByText("1.5%")).toBeInTheDocument();
    expect(within(monthly).getByText("−0.7%")).toBeInTheDocument();             // Nifty under the fund
  });

  it("switches the whole page to the current year", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "2027 to date" }));
    expect(screen.getByRole("button", { name: "2027 to date" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByText("+4.20%").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /^Growth/ }));            // the period carries into a book
    expect(screen.getAllByText("+7.70%").length).toBeGreaterThan(0);
    expect(screen.getByText("Closed positions since 31 Dec 2026 (1)")).toBeInTheDocument();
  });

  it("shows the top 10 contributors and expands to every winner; nothing to expand under 10", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /^Growth/ }));
    expect(screen.getByText("Biggest contributors (12)")).toBeInTheDocument();
    expect(screen.getByText("Biggest detractors (2)")).toBeInTheDocument();
    // averages over the whole list (12..1 -> +6.50; -0.5 and -2 -> -1.25), not just the bars shown
    expect(screen.getByText("Dashed line: average of all 12 winners, +6.50 pts each (top 10 shown).")).toBeInTheDocument();
    expect(screen.getByText("Dashed line: average of all 2 losers, −1.25 pts each.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 12 winners" }));
    expect(screen.getByText("Dashed line: average of all 12 winners, +6.50 pts each.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show top 10" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /losers/ })).toBeNull();
  });

  it("shows an equity book with benchmarks and trade analysis", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /^Growth/ }));
    expect(screen.getByText("Equal-weight NIFTY200")).toBeInTheDocument();
    expect(screen.getByText("Closed positions (2)")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();                           // hit rate
    expect(screen.getByText("6.1× / yr")).toBeInTheDocument();                     // 5.28 over 0.87 years
    const open = screen.getByText("Open positions (1)").closest("section")!;
    expect(within(open).getByText("CCC")).toBeInTheDocument();
  });

  it("shows an asset book without trade analysis", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /^Gold/ }));
    expect(screen.getByText(/GOLDBEES/)).toBeInTheDocument();
    expect(screen.queryByText(/Closed positions/)).toBeNull();
  });

  it("says so when this year is the same period as since model start", async () => {
    vi.mocked(api.performance).mockResolvedValue({ ...PERF, periods: [ALL, { ...ALL, id: "ytd", label: "2026 to date", same_as_all: true }] });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "2026 to date" }));
    expect(screen.getByText(/same period as since model start/)).toBeInTheDocument();
  });

  it("surfaces a refused computation", async () => {
    vi.mocked(api.performance).mockImplementation(() => Promise.reject(new ApiError(409, "model_start is in the future")));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("model_start is in the future");
  });
});
