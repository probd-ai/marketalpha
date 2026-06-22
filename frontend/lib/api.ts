import type { Portfolio, RunResult, Strategy, Universe } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) { super(detail); this.name = "ApiError"; }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep default */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  async listPortfolios(): Promise<Portfolio[]> {
    return (await req<{ portfolios: Portfolio[] }>("/api/portfolio")).portfolios;
  },
  async getPortfolio(id: string): Promise<Portfolio> {
    return (await req<{ portfolio: Portfolio }>(`/api/portfolio/${id}`)).portfolio;
  },
  async createPortfolio(body: Portfolio): Promise<{ portfolio_id: string }> {
    return req("/api/portfolio", { method: "POST", body: JSON.stringify(body) });
  },
  async updatePortfolio(id: string, body: Portfolio): Promise<{ portfolio_id: string }> {
    return req(`/api/portfolio/${id}`, { method: "PUT", body: JSON.stringify(body) });
  },
  async deletePortfolio(id: string): Promise<{ success: boolean }> {
    return req(`/api/portfolio/${id}`, { method: "DELETE" });
  },
  async startPortfolioRun(id: string, endDate?: string, startDate?: string): Promise<{ job_id: string; status: string }> {
    const params = new URLSearchParams();
    if (startDate) params.set("start_date", startDate);
    if (endDate) params.set("end_date", endDate);
    const qs = params.toString();
    return req(`/api/portfolio/${id}/run${qs ? `?${qs}` : ""}`, { method: "POST" });
  },
  async getRunStatus(jobId: string): Promise<{ status: string; result: RunResult | null; error: string | null }> {
    return req(`/api/portfolio/run-status/${jobId}`);
  },
  async runPortfolio(id: string, endDate?: string, startDate?: string): Promise<RunResult> {
    const { job_id } = await api.startPortfolioRun(id, endDate, startDate);
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const maxPolls = 200; // ~6-7 min ceiling at 2s
    for (let i = 0; i < maxPolls; i++) {
      await sleep(2000);
      const s = await api.getRunStatus(job_id);
      if (s.status === "completed" && s.result) return s.result;
      if (s.status === "failed") throw new ApiError(0, s.error ?? "Run failed");
    }
    throw new ApiError(0, "Run timed out");
  },
  async listStrategies(): Promise<Strategy[]> {
    return (await req<{ strategies: Strategy[] }>("/api/simulation/strategies")).strategies;
  },
  async createStrategy(body: { name: string; description: string; rules: Strategy["rules"] }): Promise<{ strategy_id: string }> {
    return req("/api/simulation/strategies", { method: "POST", body: JSON.stringify(body) });
  },
  async listUniverses(): Promise<Universe[]> {
    return (await req<{ universes: Universe[] }>("/api/data/universes")).universes;
  },
};
