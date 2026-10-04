import { afterEach, describe, expect, it, vi } from "vitest";

import { api, priceWarnings, type TicketLine } from "@/lib/api";
import { draftFor, toItem } from "@/lib/executions";

afterEach(() => vi.restoreAllMocks());

const L = (p: Partial<TicketLine> = {}): TicketLine => ({
  line_id: "L012", sleeve: "Growth", symbol: "MOTILALOFS", side: "BUY", qty: 144, ref_px: 1029, value: 148176, limit_px: 1032.09,
  reasons: [], reason_text: "why", full_exit: false, ...p,
});

describe("toItem", () => {
  it("extra sends the full quantity bought", () => {
    expect(toItem(L(), { status: "extra", qty: "179", price: "1051" })).toEqual({
      item: { line_id: "L012", status: "extra", qty: 179, price: 1051 } });
  });
  it("extra must exceed the ticket and is BUY-only", () => {
    expect(toItem(L(), { status: "extra", qty: "144", price: "1051" })).toHaveProperty("error", expect.stringMatching(/more than/));
    expect(toItem(L({ side: "SELL" }), { status: "extra", qty: "150", price: "1" })).toHaveProperty("error", expect.stringMatching(/BUY/));
  });
  it("done takes the ticket quantity; skipped needs no price", () => {
    expect(toItem(L(), { status: "done", qty: "144", price: "1030" })).toEqual({ item: { line_id: "L012", status: "done", price: 1030 } });
    expect(toItem(L(), { status: "skipped", qty: "", price: "" })).toEqual({ item: { line_id: "L012", status: "skipped" } });
  });
  it("partial stays strictly inside the ticket quantity", () => {
    expect(toItem(L(), { status: "partial", qty: "143", price: "1" })).toHaveProperty("item");
    expect(toItem(L(), { status: "partial", qty: "144", price: "1" })).toHaveProperty("error");
    expect(toItem(L(), { status: "partial", qty: "2.5", price: "1" })).toHaveProperty("error");
  });
  it("requires a price for anything but skipped", () => {
    expect(toItem(L(), { status: "done", qty: "", price: "" })).toHaveProperty("error", expect.stringMatching(/price/));
  });
  it("draftFor restores a recorded extra", () => {
    expect(draftFor(L(), { line_id: "L012", status: "extra", qty: 179, price: 1051, extra_qty: 35 })).toEqual(
      { status: "extra", qty: "179", price: "1051" });
  });
});

describe("price warnings", () => {
  it("a 409 carries reviewable warnings, and confirm resends with ?confirm=true", async () => {
    const w = [{ line_id: "L013", symbol: "PAYTM", price: 1323, ref_px: 1674, low: 1624.4, high: 1708, reason: "not traded" }];
    const f = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { message: "fill prices need review", warnings: w } }), { status: 409 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "EXECUTING", recorded: 1, lines: 47 })));
    const items = [{ line_id: "L013", status: "done" as const, price: 1323 }];
    const err = await api.record("R1", items).catch((e) => e);
    expect(err.message).toBe("fill prices need review");
    expect(priceWarnings(err)).toEqual(w);
    await api.record("R1", items, true);
    expect(f.mock.calls[1][0]).toBe("/api/fund/runs/R1/executions?confirm=true");
  });
  it("other errors are not warnings", () => {
    expect(priceWarnings(new Error("x"))).toBeNull();
  });
});
