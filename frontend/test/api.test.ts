import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { api, ApiError } from "@/lib/api";

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300, status,
    json: async () => body, text: async () => JSON.stringify(body),
  });
}

function mockFetchSequence(responses: Array<{ status: number; body: unknown }>) {
  let call = 0;
  return vi.fn().mockImplementation(() => {
    const r = responses[Math.min(call++, responses.length - 1)];
    return Promise.resolve({
      ok: r.status >= 200 && r.status < 300, status: r.status,
      json: async () => r.body, text: async () => JSON.stringify(r.body),
    });
  });
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("api client", () => {
  it("listPortfolios returns the portfolios array", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { success: true, portfolios: [{ id: "p1", name: "A" }] }));
    const out = await api.listPortfolios();
    expect(out[0].id).toBe("p1");
    expect((fetch as any).mock.calls[0][0]).toBe("/api/portfolio");
  });

  it("runPortfolio polls job status and resolves to RunResult", async () => {
    const minimalResult = { success: true, summary: { total_return: 42 }, curve: [], current_book: [], sleeves: [], rebalance_actions: { add: [], exit: [] }, portfolio: {} };
    vi.stubGlobal("fetch", mockFetchSequence([
      { status: 202, body: { success: true, job_id: "job1", status: "running" } },
      { status: 200, body: { success: true, status: "completed", result: minimalResult, error: null } },
    ]));
    vi.useFakeTimers();
    const promise = api.runPortfolio("p1", "2026-06-20");
    // advance past the 2s sleep inside the polling loop
    await vi.advanceTimersByTimeAsync(2000);
    const out = await promise;
    expect(out.summary.total_return).toBe(42);
    const calls = (fetch as any).mock.calls;
    expect(calls[0][0]).toContain("/api/portfolio/p1/run");
    expect(calls[0][0]).toContain("end_date=2026-06-20");
    expect(calls[1][0]).toContain("/api/portfolio/run-status/job1");
  });

  it("throws ApiError with backend detail on 400", async () => {
    vi.stubGlobal("fetch", mockFetch(400, { detail: "sleeve weights must sum to 100 (got 90)" }));
    await expect(api.createPortfolio({} as any)).rejects.toMatchObject({
      status: 400, detail: expect.stringContaining("sum to 100"),
    });
    expect(ApiError).toBeTruthy();
  });
});
