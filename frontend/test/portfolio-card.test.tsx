import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PortfolioCard } from "@/components/portfolio-card";
import type { Portfolio } from "@/lib/types";

const p: Portfolio = {
  id: "p1", name: "Growth Book", description: "", total_capital: 1000000,
  benchmark_symbol: "Nifty 50", inception_date: "2024-01-01", point_in_time_membership: true,
  sleeves: [
    { id: "s1", label: "Mid", strategy_id: "x", weight: 60, universe: "NIFTYMIDCAP150",
      rebalance_frequency: "monthly", rebalance_date: "first", rebalance_type: "equal_weight",
      max_holdings: 10, momentum_ranking: "20_day_return", include_brokerage: true, exchange: "NSE" },
    { id: "s2", label: "Micro", strategy_id: "y", weight: 40, universe: "NIFTYMICROCAP250",
      rebalance_frequency: "weekly", rebalance_date: "first", rebalance_type: "equal_weight",
      max_holdings: 10, momentum_ranking: "20_day_return", include_brokerage: true, exchange: "NSE" },
  ],
};

describe("PortfolioCard", () => {
  it("shows name, sleeve count and links to detail", () => {
    render(<PortfolioCard portfolio={p} />);
    expect(screen.getByText("Growth Book")).toBeInTheDocument();
    expect(screen.getByText(/2 sleeves/)).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/portfolio/p1");
  });
});
