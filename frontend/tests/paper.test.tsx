import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PaperView } from "@/components/PaperView";

describe("PaperView", () => {
  it("renders paragraphs, notes and tables with totals", () => {
    render(<PaperView sections={[{
      heading: "Step 1 — Sell", body: "Sells first.\n\nThen buys.",
      note: { tone: "warn", text: "Prices are the 10-01 close." },
      table: { columns: ["Symbol", "Qty", "₹"], align: ["left", "right", "right"],
        rows: [["DIVISLAB", 16, 147984]], total: ["Total", null, 147984] },
    }]} />);
    expect(screen.getByRole("heading", { name: "Step 1 — Sell" })).toBeInTheDocument();
    expect(screen.getByText("Then buys.")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("⚠ Prices are the 10-01 close.");
    expect(screen.getAllByText("1,47,984")).toHaveLength(2);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("never interprets markup", () => {
    render(<PaperView sections={[{ heading: "x", body: "<img src=x onerror=alert(1)>" }]} />);
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});
