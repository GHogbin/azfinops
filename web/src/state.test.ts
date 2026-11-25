import { describe, expect, it } from "vitest";
import type { Filters } from "./api";
import { DEFAULT_ANALYSIS, fromSearch, toSearch } from "./state";

const f: Filters = {
  costKind: "amortized", currency: "GBP", start: "2026-09-01", end: "2026-09-30",
  dims: { service: ["Storage"], application: ["app-01137", "(untagged)"] },
};

describe("URL state", () => {
  it("round-trips filters and view", () => {
    const back = fromSearch(toSearch(f, "explorer"));
    expect(back.view).toBe("explorer");
    expect(back.filters).toEqual(f);
  });
  it("keeps the combined flag in the URL", () => {
    const c = { ...f, combined: true };
    expect(fromSearch(toSearch(c, "overview")).filters?.combined).toBe(true);
    expect(fromSearch(toSearch(f, "overview")).filters?.combined).toBeUndefined();
  });
  it("knows every workspace view and ignores unknown ones", () => {
    expect(fromSearch(toSearch(f, "commitments")).view).toBe("commitments");
    expect(fromSearch(toSearch(f, "spend")).view).toBe("spend");
    expect(fromSearch(toSearch(f, "applications")).view).toBe("applications");
    expect(fromSearch(toSearch(f, "ai")).view).toBe("ai");
    expect(fromSearch(toSearch(f, "optimization")).view).toBe("optimization");
    expect(fromSearch(toSearch(f, "data")).view).toBe("data");
    expect(fromSearch(toSearch(f, "knowledge")).view).toBe("knowledge");
    expect(fromSearch(toSearch(f, "pricing")).view).toBe("pricing");
    expect(fromSearch("?view=no-such-view").view).toBe("overview");
  });
  it("returns no filters when scope is missing so the app picks a default", () => {
    expect(fromSearch("").filters).toBeNull();
    expect(fromSearch("?cost_kind=amortized").filters).toBeNull();
  });
  it("drops invalid or hostile input instead of passing it on", () => {
    const r = fromSearch("?cost_kind=mixed&currency=GBP");
    expect(r.filters).toBeNull();
    const g = fromSearch("?cost_kind=actual&currency=GBP&start=yesterday&end=2026-13-01x&service=" + "x".repeat(201));
    expect(g.filters?.start).toBeNull();
    expect(g.filters?.end).toBeNull();
    expect(g.filters?.dims.service).toBeUndefined();
    expect(fromSearch("?cost_kind=actual&currency=GBP&end=2026-13-01").filters?.end).toBeNull();
    expect(fromSearch("?view=<script>").view).toBe("overview");
  });
});

describe("explorer grouping", () => {
  it("is kept in the URL for the Explorer only, and unknown values are dropped", () => {
    const f = { costKind: "amortized" as const, currency: "GBP", start: null, end: null, dims: {} };
    expect(fromSearch(toSearch(f, "explorer", "meter_subcategory")).group).toBe("meter_subcategory");
    expect(fromSearch(toSearch(f, "overview", "meter_subcategory")).group).toBeNull();
    expect(fromSearch("?view=explorer&group=nope").group).toBeNull();
  });
});

describe("cost analysis settings", () => {
  it("round-trip through the URL, and bad values fall back to the defaults", () => {
    const f = { costKind: "amortized" as const, currency: "USD", start: null, end: null, dims: {} };
    const a = { dim: "subscription" as const, gran: "month" as const, chart: "grouped" as const, compare: true, forecast: false, budget: true, dims: { service: ["Storage", "Virtual Machines"] } };
    expect(fromSearch(toSearch(f, "analysis", null, a)).analysis).toEqual(a);
    expect(fromSearch("?view=analysis&ag=nope&gr=week&ch=pie").analysis).toEqual(DEFAULT_ANALYSIS);
    expect(toSearch(f, "overview", null, a)).not.toContain("ag=");
    // chart filters stay out of the dashboard filters, and out of other views' URLs
    expect(fromSearch(toSearch(f, "analysis", null, a)).filters?.dims).toEqual({});
    expect(toSearch(f, "overview", null, a)).not.toContain("af.");
  });
});
