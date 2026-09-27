import { describe, expect, it } from "vitest";

import { pct, price, qty, rupees } from "@/lib/format";

describe("format", () => {
  it("rupees uses lakh and crore", () => {
    expect(rupees(4_500_000)).toBe("₹45.00 L");
    expect(rupees(24_167_152)).toBe("₹2.42 Cr");
    expect(rupees(12_345, { compact: false })).toBe("₹12,345");
    expect(rupees(-150_000)).toBe("−₹1.50 L");
    expect(rupees(null)).toBe("—");
  });
  it("pct signs and missing values", () => {
    expect(pct(4.123, 1, true)).toBe("+4.1%");
    expect(pct(-2.5)).toBe("−2.5%");
    expect(pct(Number.NaN)).toBe("—");
  });
  it("qty and price", () => {
    expect(qty(1234.6)).toBe("1,235");
    expect(price(1221.25)).toBe("1,221.25");
  });
});
