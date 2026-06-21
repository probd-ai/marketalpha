import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
// Recharts needs sizing; mock ResponsiveContainer to a fixed box.
vi.mock("recharts", async (orig) => {
  const actual = await orig<typeof import("recharts")>();
  return { ...actual, ResponsiveContainer: ({ children }: any) =>
    <div style={{ width: 600, height: 300 }}>{children}</div> };
});
import { PerfCurve } from "@/components/detail/perf-curve";

describe("PerfCurve", () => {
  it("renders a chart wrapper for given points", () => {
    render(<PerfCurve curve={[
      { date: "2024-01-01", portfolio_value: 1000000, benchmark_value: 1000000 },
      { date: "2024-06-30", portfolio_value: 1421000, benchmark_value: 1163000 },
    ]} />);
    expect(screen.getByTestId("perf-curve")).toBeInTheDocument();
  });
});
