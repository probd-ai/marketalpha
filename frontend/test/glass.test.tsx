import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Stat, Badge } from "@/components/ui/glass";

describe("glass primitives", () => {
  it("Stat shows label and value", () => {
    render(<Stat label="Sharpe" value="1.07" />);
    expect(screen.getByText("Sharpe")).toBeInTheDocument();
    expect(screen.getByText("1.07")).toBeInTheDocument();
  });
  it("Badge renders its text", () => {
    render(<Badge tone="warn">HYPOTHETICAL · PIT</Badge>);
    expect(screen.getByText("HYPOTHETICAL · PIT")).toBeInTheDocument();
  });
});
