export const EARLIEST_INCEPTION = "2022-01-01";

export function formatINR(n: number): string {
  const sign = n < 0 ? "-" : "";
  return sign + "₹" + Math.round(Math.abs(n)).toLocaleString("en-IN");
}

export function formatLakh(n: number): string {
  const sign = n < 0 ? "-" : "";
  const a = Math.abs(n);
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)}Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)}L`;
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
