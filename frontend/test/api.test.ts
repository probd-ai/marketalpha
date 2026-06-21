import { describe, it, expect, vi, afterEach } from "vitest";
import { api, ApiError } from "@/lib/api";

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300, status,
    json: async () => body, text: async () => JSON.stringify(body),
  });
}
afterEach(() => vi.restoreAllMocks());

describe("api client", () => {
  it("listPortfolios returns the portfolios array", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { success: true, portfolios: [{ id: "p1", name: "A" }] }));
    const out = await api.listPortfolios();
    expect(out[0].id).toBe("p1");
    expect((fetch as any).mock.calls[0][0]).toBe("/api/portfolio");
  });

  it("runPortfolio passes end_date and unwraps the run payload", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { success: true, summary: { total_return: 42 }, curve: [], current_book: [], sleeves: [], rebalance_actions: { add: [], exit: [] }, portfolio: {} }));
    const out = await api.runPortfolio("p1", "2026-06-20");
    expect(out.summary.total_return).toBe(42);
    expect((fetch as any).mock.calls[0][0]).toContain("end_date=2026-06-20");
  });

  it("throws ApiError with backend detail on 400", async () => {
    vi.stubGlobal("fetch", mockFetch(400, { detail: "sleeve weights must sum to 100 (got 90)" }));
    await expect(api.createPortfolio({} as any)).rejects.toMatchObject({
      status: 400, detail: expect.stringContaining("sum to 100"),
    });
    expect(ApiError).toBeTruthy();
  });
});
