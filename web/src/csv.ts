/** CSV for the SQL workbench download. Cells that a spreadsheet would run as a formula get a leading quote. */
export type Cell = string | number | boolean | null;

export function csvCell(v: Cell): string {
  if (v === null) return "";
  let s = String(v);
  if (typeof v === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(columns: string[], rows: Cell[][]): string {
  return [columns.map(csvCell).join(","), ...rows.map((r) => r.map(csvCell).join(","))].join("\r\n") + "\r\n";
}
