import { describe, expect, it } from "vitest";
import { filterParams, type Filters } from "./api";
import { compact, currencySymbol, dateLabel, lastDayOfMonth, latestCompleteMonth, money, moneyShort, monthsBetween, niceTicks, num, pct, periodLabel, periodPresets, previousMonth } from "./format";

describe("format", () => {
  it("never turns bad input into NaN", () => {
    expect(num("2220305.7300")).toBe(2220305.73);
    expect(num(null)).toBe(0);
    expect(num("abc")).toBe(0);
  });
  it("formats money in the native currency", () => {
    expect(money("2220305.73", "GBP")).toBe("£2,220,306");
    expect(money("1.6", "GBP", 2)).toBe("£1.60");
    expect(money("-0.3", "GBP")).toBe("£0");           // no "-£0"
    expect(money("-0.004", "GBP", 2)).toBe("£0.00");
    expect(money("-607.4", "GBP")).toBe("-£607");
    expect(money("5", "ZZZ", 0)).toContain("5");    // unknown code must not throw
  });
  it("compacts and rounds percentages", () => {
    expect(compact(2_220_305)).toBe("2.2M");
    expect(compact(340_000)).toBe("340k");
    expect(compact(-1500)).toBe("-1.5k");
    expect(pct(0.96251)).toBe("96.3%");
    expect(pct(null)).toBe("n/a");
  });
  it("formats ISO dates without timezone drift", () => {
    expect(dateLabel("2026-09-02")).toBe("2 Sep 2026");
    expect(dateLabel("2026-09-30", false)).toBe("30 Sep");
    expect(lastDayOfMonth("2026-02-01")).toBe("2026-02-28");
    expect(lastDayOfMonth("2028-02-01")).toBe("2028-02-29");
  });
  it("lists the months a date range touches", () => {
    expect(monthsBetween("2025-11-20", "2026-02-03")).toEqual(["2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"]);
    expect(monthsBetween("2026-09-01", "2026-09-30")).toEqual(["2026-09-01"]);
    expect(monthsBetween("bad", "2026-09-30")).toEqual([]);
  });
  it("produces round ticks that cover the range", () => {
    const t = niceTicks(0, 87_000, 5);
    expect(t[0]).toBe(0);
    expect(t.every((v, i) => i === 0 || v > (t[i - 1] ?? 0))).toBe(true);
    expect(t[t.length - 1]).toBeLessThanOrEqual(87_000);
    expect(niceTicks(5, 5)).toEqual([5]);
  });
});

describe("filterParams", () => {
  const f: Filters = {
    costKind: "amortized", currency: "GBP", start: "2026-09-01", end: null,
    dims: { service: ["Storage", "SaaS"], subscription: ["abc"] },
  };
  it("sends the basis, currency, dates and repeated dimension values", () => {
    const p = filterParams(f);
    expect(p.get("cost_kind")).toBe("amortized");
    expect(p.get("currency")).toBe("GBP");
    expect(p.get("start")).toBe("2026-09-01");
    expect(p.has("end")).toBe(false);
    expect(p.getAll("service")).toEqual(["Storage", "SaaS"]);
  });
  it("can leave one dimension out (a picker ignores its own selection)", () => {
    expect(filterParams(f, "service").has("service")).toBe(false);
    expect(filterParams(f, "service").getAll("subscription")).toEqual(["abc"]);
  });
  it("keeps hostile characters as data in the query string", () => {
    const p = filterParams({ ...f, dims: { service: ["a&b=c; DROP"] } });
    expect(p.toString()).toContain("service=a%26b%3Dc%3B+DROP");
  });
  it("shortens headline money without losing the sign", () => {
    expect(moneyShort("2220305.73", "GBP")).toBe("£2.22M");
    expect(moneyShort("-1500000", "GBP")).toBe("-£1.5M");
    expect(moneyShort("12", "ZZZ").length).toBeGreaterThan(0);
  });
});

describe("periods", () => {
  it("labels what a figure covers", () => {
    expect(periodLabel("2026-09-01", "2026-09-30", [])).toBe("Sep 2026");
    expect(periodLabel(null, null, ["2026-08-01", "2026-09-01"])).toBe("Aug–Sep 2026");
    expect(periodLabel("2026-11-01", "2027-01-31", [])).toBe("Nov 2026–Jan 2027");
    expect(periodLabel("2026-08-03", "2026-09-17", [])).toBe("3 Aug – 17 Sep 2026");
    expect(periodLabel(null, null, [])).toBe("");
  });
  it("finds the newest complete month", () => {
    expect(latestCompleteMonth("2026-08-01", "2026-09-30")).toEqual({ start: "2026-09-01", end: "2026-09-30" });
    expect(latestCompleteMonth("2026-08-01", "2026-10-14")).toEqual({ start: "2026-09-01", end: "2026-09-30" });
    expect(latestCompleteMonth("2026-01-01", "2026-01-20")).toBeNull();
    expect(latestCompleteMonth("2025-12-01", "2026-01-20")).toEqual({ start: "2025-12-01", end: "2025-12-31" });
  });
  it("gives the month before only for a single whole month", () => {
    expect(previousMonth("2026-09-01", "2026-09-30")).toEqual({ start: "2026-08-01", end: "2026-08-31" });
    expect(previousMonth("2026-01-01", "2026-01-31")).toEqual({ start: "2025-12-01", end: "2025-12-31" });
    expect(previousMonth("2026-08-01", "2026-09-30")).toBeNull();
    expect(previousMonth(null, null)).toBeNull();
  });
});

describe("period presets", () => {
  it("are worked out from the data", () => {
    const p = periodPresets("2026-08-01", "2026-09-30", ["2026-08-01", "2026-09-01"]);
    expect(p.map((x) => x.id)).toEqual(["latest", "last3", "all"]);     // year to date would repeat Aug–Sep
    expect(p[0]).toMatchObject({ start: "2026-09-01", end: "2026-09-30" });
    expect(p[1]).toMatchObject({ label: "Last 2 months", start: "2026-08-01", end: "2026-09-30" });
  });
  it("offer month to date when the newest month is partial", () => {
    const p = periodPresets("2026-01-01", "2026-10-14", monthsBetween("2026-01-01", "2026-09-30"));
    expect(p.find((x) => x.id === "mtd")).toMatchObject({ start: "2026-10-01", end: "2026-10-14" });
    expect(p.find((x) => x.id === "last3")).toMatchObject({ start: "2026-07-01", end: "2026-09-30" });
    expect(p.find((x) => x.id === "ytd")).toMatchObject({ start: "2026-01-01" });
  });
  it("still offer All when no month is complete", () => {
    expect(periodPresets("2026-10-01", "2026-10-14", []).map((x) => x.id)).toEqual(["mtd", "all"]);
  });
  it("does not infer completeness from a full-looking date span or bridge a missing month", () => {
    expect(periodPresets("2026-01-01", "2026-09-30", []).map((x) => x.id)).toEqual(["all"]);
    const p = periodPresets("2026-01-01", "2026-09-30", ["2026-07-01", "2026-09-01"]);
    expect(p.map((x) => x.id)).toEqual(["latest", "all"]);
  });
});

describe("currencySymbol", () => {
  it("gives the symbol, or the code when there is none", () => {
    expect(currencySymbol("GBP")).toBe("£");
    expect(currencySymbol("USD")).toBe("$");
    expect(currencySymbol("ZZZ")).toBe("ZZZ");
  });
});

describe("dollar amounts", () => {
  it("read $ rather than US$", () => {
    expect(money(1234, "USD")).toBe("$1,234");
    expect(moneyShort(16480000, "USD")).toBe("$16.48M");
    expect(money(-5, "GBP")).toBe("-£5");
  });
});
