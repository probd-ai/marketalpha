import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PortfolioForm } from "@/components/form/portfolio-form";

vi.mock("@/lib/queries", async (orig) => {
  const actual = await orig<typeof import("@/lib/queries")>();
  return {
    ...actual,
    useStrategies: () => ({ data: [{ id: "s1", name: "Aligned", description: "", rules: [] }] }),
    useUniverses: () => ({ data: [{ value: "NIFTY500", label: "NIFTY500 (500)", stock_count: 500 }] }),
    useCreatePortfolio: () => ({ mutateAsync: vi.fn().mockResolvedValue({ portfolio_id: "p9" }), isPending: false }),
    useUpdatePortfolio: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useCreateStrategy: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;
}

describe("PortfolioForm", () => {
  it("blocks save when weights do not sum to 100", () => {
    render(wrap(<PortfolioForm onSaved={() => {}} />));
    // add a second sleeve (weight 0 → sum still 100), then set its weight to 50 → sum = 150
    fireEvent.click(screen.getByText(/add sleeve/i));
    const weightInputs = screen.getAllByTitle("weight %");      // [existing=100, new=0]
    fireEvent.change(weightInputs[weightInputs.length - 1], { target: { value: "50" } }); // sum=150
    fireEvent.click(screen.getByText(/save/i));
    expect(screen.getByText(/sum to 100/i)).toBeInTheDocument();
  });
});
