export const EARLIEST_INCEPTION = "2022-01-01";

export function formatINR(n: number): string {
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

export function formatLakh(n: number): string {
  if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)}Cr`;
  if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(2)}L`;
  return formatINR(n);
}

export function formatPct(n: number, withSign = true): string {
  const s = withSign && n > 0 ? "+" : "";
  return `${s}${n.toFixed(1)}%`;
}

export function defaultInception(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}
