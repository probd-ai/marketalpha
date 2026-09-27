import type { FundConfig } from "./api";

/** Settings that change the model itself (the backend starts a new model version when any of these change). */
export const MATERIAL: (keyof FundConfig)[] = [
  "capital", "model_start", "sleeves", "backfill_weekday", "slippage_bp", "reset_cost", "rebalance_months", "rebalance_band",
];

export function isMaterialChange(a: FundConfig, b: FundConfig): boolean {
  return MATERIAL.some((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
}
