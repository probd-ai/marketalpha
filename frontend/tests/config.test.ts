import { describe, expect, it } from "vitest";

import type { FundConfig } from "@/lib/api";
import { isMaterialChange } from "@/lib/config";

const base: FundConfig = {
  name: "F", capital: 100, model_start: "2020-10-01", backfill_weekday: 0, slippage_bp: 5, reset_cost: 0.0015,
  limit_band: 0.003, rebalance_months: [1, 7], rebalance_band: 0.05, version: 1, sleeves: [{ id: "v", label: "V", kind: "grid10", universe: "NIFTY50", weight: 1 }],
};

describe("isMaterialChange", () => {
  it("name and limit band are not material", () => {
    expect(isMaterialChange(base, { ...base, name: "G", limit_band: 0.005 })).toBe(false);
  });
  it("model start, capital and sleeve weights are material", () => {
    expect(isMaterialChange(base, { ...base, model_start: "2024-01-01" })).toBe(true);
    expect(isMaterialChange(base, { ...base, capital: 200 })).toBe(true);
    expect(isMaterialChange(base, { ...base, sleeves: [{ ...base.sleeves[0], weight: 0.9 }] })).toBe(true);
  });
});

describe("rebalance settings", () => {
  it("rebalance months and band are material", () => {
    const b = { ...base, rebalance_months: [1, 7], rebalance_band: 0.05 };
    expect(isMaterialChange(b, { ...b, rebalance_months: [1, 4, 7, 10] })).toBe(true);
    expect(isMaterialChange(b, { ...b, rebalance_band: null })).toBe(true);
  });
});
