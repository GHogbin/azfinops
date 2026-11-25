import { describe, expect, it } from "vitest";
import { accumulate, alignPrevious, budgetLine, daysToMonthEnd, forecast, NO_MATCH, withLocalFilters } from "./analysis";

describe("cost analysis helpers", () => {
  it("accumulates", () => expect(accumulate([1, 2, -1, 4])).toEqual([1, 3, 2, 6]));

  it("forecasts the rest of the month at the recent daily average", () => {
    const days = ["2026-10-27", "2026-10-28", "2026-10-29"];
    const f = forecast(days, [10, 20, 30], null, 2);
    expect(f?.days).toEqual(["2026-10-30", "2026-10-31"]);
    expect(f?.rate).toBe(25);
    expect(f?.values).toEqual([85, 110]);
    expect(forecast(["2026-09-30"], [5], null)).toBeNull();            // month complete
    expect(forecast(days, [10, 20, 30], "2026-10-29")).toBeNull();     // range ends before month end
    expect(daysToMonthEnd("2026-02-27")).toEqual(["2026-02-28"]);
  });

  it("spreads a monthly budget over days, months or a running total", () => {
    expect(budgetLine(["2026-09-01", "2026-10-01"], "day", 3000)).toEqual([100, 3000 / 31]);
    expect(budgetLine(["2026-09-01", "2026-10-01"], "month", 3000)).toEqual([3000, 3000]);
    expect(budgetLine(["2026-09-29", "2026-09-30", "2026-10-01"], "acc", 3000)).toEqual([3000, 3000, 6000]);
  });

  it("lines the previous period up day by day", () => {
    expect(alignPrevious([1, 2, 3], 4, true)).toEqual([1, 3, 6, null]);
    expect(alignPrevious([1, 2, 3], 2, false)).toEqual([1, 2]);
  });
});

describe("chart-only filters", () => {
  const f = { costKind: "amortized", currency: "USD", start: null, end: null, dims: { subscription: ["a", "b"] } as Record<string, string[]> };
  it("add to the dashboard filters without changing them", () => {
    const m = withLocalFilters(f, { service: ["Storage"] });
    expect(m.dims).toEqual({ subscription: ["a", "b"], service: ["Storage"] });
    expect(f.dims).toEqual({ subscription: ["a", "b"] });
  });
  it("keep only values in both when the same field is filtered twice", () => {
    expect(withLocalFilters(f, { subscription: ["b", "c"] }).dims.subscription).toEqual(["b"]);
    expect(withLocalFilters(f, { subscription: ["c"] }).dims.subscription).toEqual([NO_MATCH]);
  });
  it("ignore empty selections", () => {
    expect(withLocalFilters(f, { service: [] }).dims).toEqual(f.dims);
  });
});
