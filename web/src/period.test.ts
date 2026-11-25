import { describe, expect, it } from "vitest";
import type { Filters, Meta } from "./api";
import { completeMonths } from "./components/PeriodPicker";

const filters: Filters = { costKind: "amortized", currency: "GBP", start: null, end: null, dims: {} };
const meta: Meta = {
  scopes: [], billing_accounts: ["a", "b"], dimensions: [],
  scope_months: [
    { month: "2026-08-01", cost_kind: "amortized", account: "a", currency: "GBP", complete: true },
    { month: "2026-09-01", cost_kind: "amortized", account: "a", currency: "GBP", complete: true },
    { month: "2026-09-01", cost_kind: "amortized", account: "b", currency: "USD", complete: false },
  ],
};

describe("scoped complete months", () => {
  it("does not let another currency certify a partial enrollment", () => {
    expect(completeMonths(meta, filters)).toEqual(["2026-08-01", "2026-09-01"]);
    expect(completeMonths(meta, { ...filters, combined: true })).toEqual([]);
    expect(completeMonths(meta, { ...filters, combined: true, dims: { billing_account: ["a"] } }))
      .toEqual(["2026-08-01", "2026-09-01"]);
  });
  it("requires explicit scope coverage instead of guessing from date boundaries", () => {
    expect(completeMonths({ ...meta, scope_months: undefined }, filters)).toEqual([]);
  });
});
