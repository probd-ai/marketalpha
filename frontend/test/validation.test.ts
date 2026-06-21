import { describe, it, expect } from "vitest";
import { sleeveWeightSum, validatePortfolioForm } from "@/lib/validation";

const base = {
  name: "P",
  inception_date: "2024-01-01",
  sleeves: [
    { strategy_id: "s1", universe: "NIFTY500", weight: 60 },
    { strategy_id: "s2", universe: "NIFTYMIDCAP150", weight: 40 },
  ],
};

describe("validation", () => {
  it("sums sleeve weights", () => {
    expect(sleeveWeightSum(base.sleeves)).toBe(100);
  });
  it("accepts a valid form", () => {
    expect(validatePortfolioForm(base as any)).toEqual([]);
  });
  it("flags weight sum != 100", () => {
    const bad = {
      ...base,
      sleeves: [{ ...base.sleeves[0], weight: 50 }, base.sleeves[1]],
    };
    expect(validatePortfolioForm(bad as any).some((e) => e.includes("100"))).toBe(
      true
    );
  });
  it("flags early inception", () => {
    expect(
      validatePortfolioForm({
        ...base,
        inception_date: "2021-01-01",
      } as any).some((e) => e.includes("2022"))
    ).toBe(true);
  });
  it("flags empty name and no sleeves", () => {
    expect(
      validatePortfolioForm({
        name: "",
        inception_date: "2024-01-01",
        sleeves: [],
      } as any).length
    ).toBeGreaterThanOrEqual(2);
  });
});
