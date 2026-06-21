import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { KpiStrip } from "@/components/detail/kpi-strip";
import { HoldingsTable } from "@/components/detail/holdings-table";
import { SleeveAttributionPanel } from "@/components/detail/sleeve-attribution";

describe("KpiStrip", () => {
  it("renders return and alpha", () => {
    render(<KpiStrip summary={{ total_value: 1421000, total_pnl: 421000, total_return: 42.1,
      benchmark_return: 16.3, alpha: 25.8, max_drawdown: -24.6, sharpe_ratio: 1.07 }} totalCapital={1000000} />);
    expect(screen.getByText("+42.1%")).toBeInTheDocument();
    expect(screen.getByText("+25.8%")).toBeInTheDocument();
    expect(screen.getByText("1.07")).toBeInTheDocument();
  });
});

describe("HoldingsTable", () => {
  it("renders holdings sorted by market value", () => {
    render(<HoldingsTable book={[
      { symbol: "BSE", sleeve: "Mid", quantity: 1, current_price: 1, market_value: 39780, weight: 2.8, pnl: 100, pnl_percent: 8.3 },
      { symbol: "DIXON", sleeve: "Mid", quantity: 1, current_price: 1, market_value: 44020, weight: 3.1, pnl: 500, pnl_percent: 12.4 },
    ]} />);
    const rows = screen.getAllByRole("row");
    // header + 2 data rows; first data row is DIXON (higher mkt value)
    expect(rows[1]).toHaveTextContent("DIXON");
  });
});

describe("SleeveAttributionPanel", () => {
  it("shows contribution and coverage", () => {
    render(<SleeveAttributionPanel sleeves={[
      { id: "s1", label: "Mid", weight: 60, total_return: 58, contribution: 34.8, holdings_count: 10,
        coverage: { total_ever_members: 200, covered: 192, missing: ["X","Y","Z","W","V","U","T","B"] } },
    ]} />);
    expect(screen.getByText(/\+34.8/)).toBeInTheDocument();
    expect(screen.getByText(/coverage/i)).toBeInTheDocument();
  });
});
