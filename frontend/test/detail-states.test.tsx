import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { KpiStrip } from "@/components/detail/kpi-strip";

describe("KpiStrip", () => {
  it("renders return and alpha", () => {
    render(<KpiStrip summary={{ total_value: 1421000, total_pnl: 421000, total_return: 42.1,
      benchmark_return: 16.3, alpha: 25.8, max_drawdown: -24.6, sharpe_ratio: 1.07 }} />);
    expect(screen.getByText("+42.1%")).toBeInTheDocument();
    expect(screen.getByText("+25.8%")).toBeInTheDocument();
    expect(screen.getByText("1.07")).toBeInTheDocument();
  });
});
