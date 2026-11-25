import { describe, expect, it } from "vitest";
import { csvCell, toCsv } from "./csv";

describe("csv", () => {
  it("quotes commas, quotes and newlines", () => {
    expect(csvCell("a,b")).toBe('"a,b"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell("l1\nl2")).toBe('"l1\nl2"');
  });
  it("renders null as empty and keeps numbers and booleans", () => {
    expect(csvCell(null)).toBe("");
    expect(csvCell(12.5)).toBe("12.5");
    expect(csvCell(false)).toBe("false");
  });
  it("neutralises spreadsheet formulas in text but not negative numbers", () => {
    expect(csvCell("=SUM(A1)")).toBe("'=SUM(A1)");
    expect(csvCell("@x")).toBe("'@x");
    expect(csvCell("-1+1")).toBe("'-1+1");
    expect(csvCell(-3)).toBe("-3");
  });
  it("builds a header and rows with CRLF", () => {
    expect(toCsv(["a", "b"], [[1, "x"], [null, "y,z"]])).toBe('a,b\r\n1,x\r\n,"y,z"\r\n');
  });
});
