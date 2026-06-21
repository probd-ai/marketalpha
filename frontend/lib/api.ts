import type { Portfolio, RunResult, Strategy, Universe } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) { super(detail); this.name = "ApiError"; }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
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
  async runPortfolio(id: string, endDate?: string): Promise<RunResult> {
    const q = endDate ? `?end_date=${endDate}` : "";
    return req<RunResult>(`/api/portfolio/${id}/run${q}`, { method: "POST" });
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
