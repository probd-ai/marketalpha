/** Typed client for the market-hunt /api/fund endpoints (proxied by next.config rewrites). */

export type Status = "PASS" | "WARN" | "BLOCK";
export type RunStatus = "DRAFT" | "PUBLISHED" | "EXECUTING" | "CLOSED" | "DISCARDED";

export interface Check { name: string; status: Status; detail: string; items: unknown[] }
export interface TicketLine {
  line_id: string; sleeve: string; symbol: string; side: "BUY" | "SELL"; qty: number; ref_px: number;
  value: number; limit_px: number; reasons: string[]; reason_text: string; full_exit: boolean;
}
export interface Holding {
  symbol: string; shares: number; price: number; value: number; weight: number; lots: number; oldest_age: number;
  held_since: string; roc22?: number; grid?: number; alpha?: boolean;
}
export interface Book {
  id: string; label: string; kind: "grid10" | "tranche" | "asset"; target_weight: number; weight: number;
  value: number; cash: number; holdings: Holding[]; curve_cagr: number | null;
}
export interface Perf { cagr: number | null; total_return: number; max_dd: number; dd_now: number; r_1m: number; r_3m: number; r_1y: number }
export interface FundSummary extends Perf {
  capital: number; value: number; value_before_flows?: number; net_invested?: number; live_since: string | null; nifty: Perf; model_start: string;
}
export interface SleeveSummary { id: string; label: string; target_weight: number; weight: number; value: number; cagr: number | null }
export interface Curve { dates: string[]; fund: number[]; nifty: number[] }
export type ExecStatus = "done" | "partial" | "extra" | "skipped";
export interface Execution {
  line_id: string; status: ExecStatus; qty: number; price: number | null; note?: string; extra_qty?: number;
  price_warning?: string;
}
export interface ExecutionItem { line_id: string; status: ExecStatus; qty?: number; price?: number }
export interface PriceWarning {
  line_id: string; symbol: string; price: number; ref_px: number; low: number | null; high: number | null; reason: string;
}
export interface OtherRow {
  symbol: string; qty: number; cost_basis: number | null; note?: string; source?: string; price: number | null; value: number | null;
}
export interface OtherHoldings { manual: OtherRow[]; from_tickets: OtherRow[]; value: number }
export interface Run {
  _id: string; version: number; engine_version: string; status: RunStatus; run_type: string; signal_date: string;
  fill_date: string; iso_week: string; monthly_reset: boolean; initial_build?: boolean;
  rebalance?: { due: boolean; why: string | null; drift_pts: number; months: number[]; band_pts: number | null }; verdict: Status; checks: Check[]; ticket: TicketLine[];
  residuals: { sleeve: string; symbol: string; model_shares: number }[]; fund: FundSummary; sleeves: SleeveSummary[];
  books: Book[]; curve: Curve; created_at: string; published_at?: string; executions?: Execution[]; fingerprint: string;
  drift_hash?: string | null;
}
export type RunListItem = Omit<Run, "ticket" | "books" | "curve" | "residuals">;
export interface Job { job_id: string; status: "running" | "done" | "refused" | "error"; message: string; result?: string; error?: string }
export interface DeskStatus {
  config_version: number; fund_name: string; capital: number; model_start: string; latest_prices: string | null;
  today: string; last_run: RunListItem | null; last_published: RunListItem | null; open_tickets: string[]; job: Job | null;
}
export interface SleeveConfig {
  id: string; label: string; kind: "grid10" | "tranche" | "asset"; universe?: string; symbol?: string; weight: number;
  params?: Record<string, number>;
}
export interface FundConfig {
  name: string; capital: number; model_start: string; backfill_weekday: number; slippage_bp: number;
  reset_cost: number; limit_band: number; rebalance_months: number[]; rebalance_band: number | null; version: number;
  sleeves: SleeveConfig[]; updated_at?: string;
}
export interface ActualRow { sleeve: string; symbol: string; actual_qty: number; model_qty: number; gap_qty: number; gap_value: number | null; price: number | null }
export interface SleeveTracking { sleeve: string; model_value: number; actual_value: number; under_value: number; over_value: number; unpriced: number }
export interface Actual {
  as_of_run: string | null; rows: ActualRow[]; tracking_value: number; sleeves: SleeveTracking[]; undeployed: number;
  other_value: number;
}
export type Cell = string | number | null;
export interface PaperSection {
  heading: string; body?: string; note?: { tone: "info" | "warn"; text: string };
  table?: { columns: string[]; rows: Cell[][]; align?: ("left" | "right")[]; total?: Cell[] };
}
export interface Paper {
  _id: string; title: string; kind: "trade-plan" | "research" | "note"; summary?: string; run_id?: string;
  author: string; status: "PUBLISHED" | "ARCHIVED"; created_at: string; archived_at?: string; sections?: PaperSection[];
}
export interface CashFlow {
  _id: string; amount: number; note: string; status: "PENDING" | "APPLIED" | "CANCELLED"; created_at: string;
  applied_run_id?: string; nav?: number; units?: number;
}
export interface JournalEntry { at: string; event: string; detail: Record<string, unknown> }

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) { super(message); }
}

/** The fill-price warnings carried by a 409 from the executions endpoint, or null for any other error. */
export function priceWarnings(e: unknown): PriceWarning[] | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null;
  const d = e.detail as { warnings?: PriceWarning[] } | undefined;
  return Array.isArray(d?.warnings) ? d!.warnings : null;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  // X-Fund-Desk marks requests from this app; the backend refuses state changes without it (CSRF guard).
  const r = await fetch(path, { ...init, headers: { "Content-Type": "application/json", "X-Fund-Desk": "1", ...(init?.headers ?? {}) } });
  if (!r.ok) {
    let msg = r.statusText;
    let detail: unknown;
    try {
      const b = await r.json();
      detail = b.detail;
      msg = typeof b.detail === "string" ? b.detail : typeof b.detail?.message === "string" ? b.detail.message : JSON.stringify(b.detail ?? b);
    } catch { /* keep statusText */ }
    throw new ApiError(r.status, msg, detail);
  }
  return r.json() as Promise<T>;
}

export const api = {
  status: () => req<DeskStatus>("/api/fund/status"),
  config: () => req<FundConfig>("/api/fund"),
  saveConfig: (c: Partial<FundConfig>) => req<FundConfig>("/api/fund", { method: "PUT", body: JSON.stringify(c) }),
  startRun: () => req<{ job_id: string }>("/api/fund/runs", { method: "POST" }),
  job: (id: string) => req<Job>(`/api/fund/jobs/${id}`),
  runs: () => req<RunListItem[]>("/api/fund/runs"),
  run: (id: string) => req<Run>(`/api/fund/runs/${id}`),
  publish: (id: string) => req<Run>(`/api/fund/runs/${id}/publish`, { method: "POST" }),
  discard: (id: string) => req<{ status: string }>(`/api/fund/runs/${id}/discard`, { method: "POST" }),
  ackDrift: (runId: string) => req<{ acknowledged: string }>("/api/fund/drift/ack", { method: "POST", body: JSON.stringify({ run_id: runId }) }),
  record: (id: string, items: ExecutionItem[], confirm = false) =>
    req<{ status: RunStatus; recorded: number; lines: number }>(`/api/fund/runs/${id}/executions${confirm ? "?confirm=true" : ""}`,
      { method: "POST", body: JSON.stringify(items) }),
  books: () => req<{ run_id: string | null; signal_date?: string; fund?: FundSummary; books: Book[]; curve?: Curve; sleeves?: SleeveSummary[] }>("/api/fund/books"),
  actual: () => req<Actual>("/api/fund/actual"),
  papers: () => req<Paper[]>("/api/fund/papers"),
  paper: (id: string) => req<Paper>(`/api/fund/papers/${encodeURIComponent(id)}`),
  archivePaper: (id: string) => req<{ status: string }>(`/api/fund/papers/${encodeURIComponent(id)}/archive`, { method: "POST" }),
  otherHoldings: () => req<OtherHoldings>("/api/fund/other-holdings"),
  saveOtherHoldings: (items: Record<string, { qty: number; cost_basis?: number | null; note?: string | null }>) =>
    req<OtherHoldings>("/api/fund/other-holdings", { method: "PUT", body: JSON.stringify(items) }),
  journal: () => req<JournalEntry[]>("/api/fund/journal"),
  cashflows: () => req<CashFlow[]>("/api/fund/cashflows"),
  addCashflow: (amount: number, note: string) => req<CashFlow>("/api/fund/cashflows", { method: "POST", body: JSON.stringify({ amount, note }) }),
  cancelCashflow: (id: string) => req<{ status: string }>(`/api/fund/cashflows/${id}/cancel`, { method: "POST" }),
};
