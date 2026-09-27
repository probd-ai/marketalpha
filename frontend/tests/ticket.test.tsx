import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TicketTable } from "@/components/TicketTable";
import type { TicketLine } from "@/lib/api";

const L = (p: Partial<TicketLine>): TicketLine => ({
  line_id: "L001", sleeve: "Value core", symbol: "AAA", side: "BUY", qty: 10, ref_px: 100, value: 1000, limit_px: 100.3,
  reasons: [], reason_text: "why", full_exit: false, ...p,
});

describe("TicketTable", () => {
  it("groups by sleeve and totals sells, buys and net cash", () => {
    render(<TicketTable lines={[
      L({ line_id: "L001", side: "SELL", symbol: "BBB", value: 5000, full_exit: true }),
      L({ line_id: "L002", symbol: "AAA", value: 3000 }),
      L({ line_id: "L003", sleeve: "Growth", symbol: "CCC", value: 1000 }),
    ]} />);
    expect(screen.getByText("Value core · 2 lines")).toBeInTheDocument();
    expect(screen.getByText("Growth · 1 line")).toBeInTheDocument();
    expect(screen.getByText("₹5,000", { selector: "b" })).toBeInTheDocument();
    expect(screen.getByText("₹4,000", { selector: "b" })).toBeInTheDocument();
    expect(screen.getByText("(exit all)")).toBeInTheDocument();
    expect(screen.getAllByRole("table")[0]).toHaveClass("data-table");
  const rows = within(screen.getAllByRole("table")[0]).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("SELL");
  });
});
