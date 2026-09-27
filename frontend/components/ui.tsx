"use client";

import type { ReactNode } from "react";

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-line bg-panel ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-[12.5px] font-semibold uppercase tracking-wide text-ink-2">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const TONE: Record<string, string> = {
  PASS: "bg-good-soft text-good", WARN: "bg-warn-soft text-warn", BLOCK: "bg-bad-soft text-bad",
  DRAFT: "bg-accent-soft text-accent", PUBLISHED: "bg-good-soft text-good", EXECUTING: "bg-warn-soft text-warn",
  CLOSED: "bg-panel-2 text-ink-2 border border-line", DISCARDED: "bg-panel-2 text-muted border border-line",
  BUY: "bg-accent-soft text-buy", SELL: "bg-bad-soft text-sell", done: "bg-good-soft text-good",
  partial: "bg-warn-soft text-warn", skipped: "bg-panel-2 text-muted border border-line",
};
const ICON: Record<string, string> = { PASS: "✓", WARN: "!", BLOCK: "✕" };

export function Badge({ value, label }: { value: string; label?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${TONE[value] ?? "bg-panel-2 text-ink-2"}`}>
      {ICON[value] && <span aria-hidden>{ICON[value]}</span>}
      {label ?? value}
    </span>
  );
}

export function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad" }) {
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <div className="text-[11.5px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`num mt-1 text-[22px] font-semibold leading-tight ${tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : ""}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[12px] text-muted">{sub}</div>}
    </div>
  );
}

export function Button({ children, onClick, kind = "default", disabled, type = "button", title }: {
  children: ReactNode; onClick?: () => void; kind?: "primary" | "default" | "danger"; disabled?: boolean;
  type?: "button" | "submit"; title?: string;
}) {
  const k = kind === "primary" ? "bg-accent text-white border-accent hover:opacity-90"
    : kind === "danger" ? "border-bad text-bad hover:bg-bad-soft" : "border-line bg-panel hover:bg-panel-2 text-ink";
  return (
    <button type={type} title={title} onClick={onClick} disabled={disabled}
      className={`rounded-md border px-3 py-1.5 text-[13px] font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${k}`}>
      {children}
    </button>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div role="alert" className="rounded-md border border-bad bg-bad-soft px-3 py-2 text-[13px] text-bad">{msg}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-line px-4 py-8 text-center text-muted">{children}</div>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="px-2 py-6 text-muted">{what}…</div>;
}

export function Signed({ v, children }: { v: number | null | undefined; children: ReactNode }) {
  const c = v === null || v === undefined ? "" : v > 0 ? "text-good" : v < 0 ? "text-bad" : "";
  return <span className={`num ${c}`}>{children}</span>;
}
