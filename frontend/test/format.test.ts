import { describe, it, expect } from "vitest";
import { formatINR, formatLakh, formatPct } from "@/lib/format";

describe("format", () => {
  it("formats INR with Indian grouping", () => {
    expect(formatINR(1421000)).toBe("₹14,21,000");
  });
  it("formats compact lakh/crore", () => {
    expect(formatLakh(1421000)).toBe("₹14.21L");
    expect(formatLakh(12000000)).toBe("₹1.20Cr");
  });
  it("formats percent with sign", () => {
    expect(formatPct(42.1)).toBe("+42.1%");
    expect(formatPct(-4.2)).toBe("-4.2%");
  });
});
