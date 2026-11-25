import { describe, expect, it } from "vitest";
import { begin, clearActivity, finish, labelFor, percentile, summarize, type Entry } from "./activity";

describe("activity log", () => {
  it("names known endpoints and falls back to the path", () => {
    expect(labelFor("/api/overview")).toBe("Overview");
    expect(labelFor("/api/something/else")).toBe("/api/something/else");
  });
  it("takes nearest-rank percentiles", () => {
    expect(percentile([], 0.5)).toBeNull();
    expect(percentile([10, 20, 30, 40], 0.5)).toBe(20);
    expect(percentile([10, 20, 30, 40], 0.95)).toBe(40);
  });
  it("tracks a request from running to finished", () => {
    clearActivity();
    const id = begin("GET", "/api/overview", "cost_kind=amortized");
    finish(id, "ok", 200, { totalMs: 12, dbMs: 9 });
    clearActivity();
    expect(summarize([] as Entry[]).running).toBe(0);
  });
});
