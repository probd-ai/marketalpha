import { EARLIEST_INCEPTION } from "./format";

interface SleeveLike {
  strategy_id?: string;
  universe?: string;
  weight: number;
}

interface FormLike {
  name: string;
  inception_date: string;
  sleeves: SleeveLike[];
}

export function sleeveWeightSum(sleeves: { weight: number }[]): number {
  return Math.round(sleeves.reduce((a, s) => a + (Number(s.weight) || 0), 0) * 100) / 100;
}

export function validatePortfolioForm(p: FormLike): string[] {
  const errors: string[] = [];
  if (!p.name?.trim()) errors.push("name is required");
  if (!p.sleeves?.length) errors.push("add at least one sleeve");
  if (p.sleeves?.length) {
    const sum = sleeveWeightSum(p.sleeves);
    if (Math.abs(sum - 100) > 0.5) errors.push(`sleeve weights must sum to 100 (got ${sum})`);
    for (const s of p.sleeves) {
      if (!s.strategy_id) errors.push("each sleeve needs a strategy");
      if (!s.universe) errors.push("each sleeve needs a universe");
    }
  }
  if (!p.inception_date || p.inception_date < EARLIEST_INCEPTION)
    errors.push(`inception must be on or after ${EARLIEST_INCEPTION}`);
  return errors;
}
