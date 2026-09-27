/** Display formatting. Numbers are never recomputed here — only presented. */

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** ₹ with Indian grouping; values ≥ 1 lakh shown in lakh (L) or crore (Cr). */
export function rupees(v: number | null | undefined, opts: { compact?: boolean } = {}): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "−" : "";
  const a = Math.abs(v);
  if (opts.compact !== false && a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (opts.compact !== false && a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
  return `${sign}₹${inr.format(a)}`;
}

export function pct(v: number | null | undefined, digits = 1, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const s = v.toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s.replace("-", "−")}%`;
}

export function qty(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return inr.format(Math.round(v));
}

export function price(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function dayTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
