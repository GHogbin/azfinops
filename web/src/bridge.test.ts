import { describe, expect, it } from "vitest";
import type { Movers } from "./api";
import { bridgeSteps } from "./components/WhatChanged";

const m: Movers = {
  available: true, reason: null, dimension: "service", month_from: "2026-08-01", month_to: "2026-09-01",
  total_from: "1000", total_to: "1050", change: "50", complete_from: true, complete_to: true, definition: "",
  increases: [{ key: "VM", label: null, cost_from: "400", cost_to: "480", change: "80", share: 0.2 }],
  decreases: [{ key: "SQL", label: null, cost_from: "300", cost_to: "260", change: "-40", share: -0.13 }],
};

describe("bridge", () => {
  it("runs from one total to the next, with the rest as one step", () => {
    const s = bridgeSteps(m, (r) => r.key ?? "");
    expect(s.map((x) => [x.label, x.kind, x.start, x.end])).toEqual([
      ["Aug 2026", "total", 0, 1000], ["VM", "up", 1000, 1080], ["Everything else", "other", 1080, 1090],
      ["SQL", "down", 1090, 1050], ["Sep 2026", "total", 0, 1050],
    ]);
  });
});
