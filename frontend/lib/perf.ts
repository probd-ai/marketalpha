/** Presentation helpers for the Performance page. The backend computes every figure; these only arrange them. */

import type { MonthlyRow } from "@/lib/api";

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

export type SeriesKey = Exclude<keyof MonthlyRow, "m">;

export interface YearRow {
  year: number;
  cells: (number | null)[];                // 12 months, null where the model was not running
  compare: (number | null)[];              // the same months for the comparison series
  year_return: number | null;              // months of this year compounded
  compare_year: number | null;
}

function compound(xs: (number | null)[]): number | null {
  const v = xs.filter((x): x is number => x !== null);
  if (!v.length) return null;
  return (v.reduce((a, x) => a * (1 + x / 100), 1) - 1) * 100;
}

/** Calendar grid: one row per year, newest first. A year's figure compounds only the months present, so a part
 *  year (the first year of the model) is a year-to-date figure, not an annual one. */
export function monthGrid(rows: MonthlyRow[], key: SeriesKey, compareKey?: SeriesKey): YearRow[] {
  const by = new Map<number, YearRow>();
  for (const r of rows) {
    const [y, m] = r.m.split("-").map(Number);
    if (!by.has(y)) by.set(y, { year: y, cells: Array(12).fill(null), compare: Array(12).fill(null), year_return: null, compare_year: null });
    const row = by.get(y)!;
    row.cells[m - 1] = r[key] ?? null;
    if (compareKey) row.compare[m - 1] = r[compareKey] ?? null;
  }
  return [...by.values()]
    .map((row) => ({ ...row, year_return: compound(row.cells), compare_year: compareKey ? compound(row.compare) : null }))
    .sort((a, b) => b.year - a.year);
}

/** Background for a return cell: green up / red down, deeper with size, saturating at `full` percent. */
export function heat(v: number | null | undefined, full = 8): string | undefined {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return undefined;
  const a = Math.min(Math.abs(v) / full, 1);
  const pctMix = Math.round(10 + a * 45);
  return `color-mix(in srgb, var(${v > 0 ? "--good" : "--bad"}) ${pctMix}%, transparent)`;
}

/** Percentage points, signed: "+6.44 pts". */
export function pts(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits).replace("-", "−")} pts`;
}

/** A ratio with fixed decimals ("1.15"), "—" when missing. */
export function ratio(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return v.toFixed(digits).replace("-", "−");
}

/** Turnover per year from a period figure; null for periods under a quarter (too short to annualise honestly). */
export function turnoverPerYear(turnover: number | null, years: number): number | null {
  if (turnover === null || !(years >= 0.25)) return null;
  return turnover / years;
}

/** Winners (largest gain first) and losers (largest loss first); a stock that netted exactly zero is in neither. */
export function splitPnl<T extends { pts: number }>(rows: T[]): { winners: T[]; losers: T[] } {
  return {
    winners: rows.filter((r) => r.pts > 0).sort((a, b) => b.pts - a.pts),
    losers: rows.filter((r) => r.pts < 0).sort((a, b) => a.pts - b.pts),
  };
}

/** "Jan 2026" from "2026-01". */
export function monthLabel(m: string): string {
  const [y, mo] = m.split("-").map(Number);
  return `${MONTHS[mo - 1]} ${y}`;
}
