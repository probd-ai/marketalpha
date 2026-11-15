import { describe, expect, it } from "vitest";

import { heat, monthGrid, monthLabel, pts, ratio, splitPnl, turnoverPerYear } from "@/lib/perf";

describe("monthGrid", () => {
  const rows = [
    { m: "2025-12", fund: 2, nifty: 1 },
    { m: "2026-01", fund: 10, nifty: -1 },
    { m: "2026-02", fund: -10, nifty: 2 },
  ];
  it("places months by calendar, newest year first", () => {
    const g = monthGrid(rows, "fund", "nifty");
    expect(g.map((r) => r.year)).toEqual([2026, 2025]);
    expect(g[0].cells.slice(0, 3)).toEqual([10, -10, null]);
    expect(g[1].cells[11]).toBe(2);
    expect(g[0].compare.slice(0, 2)).toEqual([-1, 2]);
  });
  it("compounds only the months present (part year = year to date)", () => {
    const g = monthGrid(rows, "fund", "nifty");
    expect(g[0].year_return).toBeCloseTo((1.1 * 0.9 - 1) * 100, 10);
    expect(g[0].compare_year).toBeCloseTo((0.99 * 1.02 - 1) * 100, 10);
    expect(g[1].year_return).toBeCloseTo(2, 10);
  });
  it("has no comparison without a compare key", () => {
    expect(monthGrid(rows, "fund")[0].compare_year).toBeNull();
  });
});

describe("helpers", () => {
  it("heat is blank for zero/missing and saturates", () => {
    expect(heat(0)).toBeUndefined();
    expect(heat(null)).toBeUndefined();
    expect(heat(4, 8)).toContain("--good) 33%");
    expect(heat(-50, 8)).toContain("--bad) 55%");
  });
  it("pts and ratio format signs", () => {
    expect(pts(6.444)).toBe("+6.44 pts");
    expect(pts(-1.3)).toBe("−1.30 pts");
    expect(pts(undefined)).toBe("—");
    expect(ratio(-0.354)).toBe("−0.35");
  });
  it("annualises turnover only over a quarter or more", () => {
    expect(turnoverPerYear(5.28, 0.87)).toBeCloseTo(6.069, 3);
    expect(turnoverPerYear(1, 0.1)).toBeNull();
    expect(turnoverPerYear(null, 1)).toBeNull();
  });
  it("splits P&L into winners (best first) and losers (worst first)", () => {
    const { winners, losers } = splitPnl([{ pts: -1 }, { pts: 3 }, { pts: 0 }, { pts: -5 }, { pts: 1 }]);
    expect(winners.map((r) => r.pts)).toEqual([3, 1]);
    expect(losers.map((r) => r.pts)).toEqual([-5, -1]);
  });
  it("labels months", () => {
    expect(monthLabel("2026-03")).toBe("Mar 2026");
  });
});
