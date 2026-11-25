import { describe, expect, it } from "vitest";
import { enrollmentSlot, segmentWidths, setAccounts } from "./components/EnrollmentSplit";

describe("enrollment splits", () => {
  it("gives negative amounts no width and keeps the rest proportional", () => {
    expect(segmentWidths([300, 100])).toEqual([75, 25]);
    expect(segmentWidths([300, -50, 100])).toEqual([75, 0, 25]);
    expect(segmentWidths([-1, 0])).toEqual([0, 0]);
  });
  it("keeps each enrollment's colour stable, whatever order the cards list them in", () => {
    setAccounts(["200001", "100001"]);
    expect(enrollmentSlot("100001")).toBe(1);
    expect(enrollmentSlot("200001")).toBe(2);
    setAccounts(["100001", "200001", "100001"]);
    expect(enrollmentSlot("200001")).toBe(2);
    const unknown = enrollmentSlot("999");
    expect(enrollmentSlot("999")).toBe(unknown);
    expect(unknown).toBeGreaterThanOrEqual(1);
    expect(unknown).toBeLessThanOrEqual(4);
  });
});
