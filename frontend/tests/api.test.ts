import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

afterEach(() => vi.restoreAllMocks());

describe("api client", () => {
  it("sends the desk header on state-changing requests", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ status: "DISCARDED" })));
    await api.discard("R1");
    const init = f.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Fund-Desk"]).toBe("1");
    expect(init.method).toBe("POST");
  });
  it("surfaces the backend's refusal message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: "blocked by pre-trade checks: Drift" }), { status: 409 }));
    await expect(api.publish("R1")).rejects.toThrow("blocked by pre-trade checks: Drift");
  });
});

describe("cash flows", () => {
  it("posts a withdrawal as a negative amount with the desk header", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ _id: "CF1" })));
    await api.addCashflow(-100000, "school fees");
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/fund/cashflows");
    expect(JSON.parse(init.body as string)).toEqual({ amount: -100000, note: "school fees" });
    expect((init.headers as Record<string, string>)["X-Fund-Desk"]).toBe("1");
  });
});
