// Typed client for the read-only API. Money/quantities arrive as exact decimal STRINGS; ratios as numbers.
// Convert with num() for display only; never accumulate sums from rounded numbers.

import { begin, finish } from "./activity";

export type CostKind = "actual" | "amortized";
export const DIMS = [
  "subscription", "application", "environment", "service", "region", "resource_group", "pricing_model",
  "publisher_type", "charge_type", "billing_account", "meter_subcategory",
] as const;
export type Dim = (typeof DIMS)[number];
export const DIM_LABEL: Record<Dim, string> = {
  subscription: "Subscription", application: "Application", environment: "Environment", service: "Service",
  region: "Region", resource_group: "Resource group", pricing_model: "Pricing model",
  publisher_type: "Publisher type", charge_type: "Charge type", billing_account: "Enrollment",
  meter_subcategory: "Meter sub-category",
};

export interface Filters {
  costKind: CostKind;
  currency: string;
  combined?: boolean; // every currency converted into `currency` with the saved monthly rates
  start: string | null; // YYYY-MM-DD
  end: string | null;
  dims: Partial<Record<Dim, string[]>>;
}

export interface Scope { cost_kind: CostKind; currency: string; first_date: string; last_date: string; rows: number; synthetic: boolean }
export interface ScopeMonth { month: string; cost_kind: CostKind; account: string; currency: string; complete: boolean }
export interface Meta { scopes: Scope[]; billing_accounts: string[]; dimensions: string[]; billing_account_names?: Record<string, string>; billing_account_currencies?: Record<string, string[]>; recovery_required?: boolean; recovery_message?: string | null; scope_months?: ScopeMonth[] }
export interface Totals { total: string; usage: string; rows: number; by_charge_type: Record<string, string> }
export interface SeriesPoint { period: string; total: string; usage: string; complete?: boolean | null }
export interface AccountCost { account: string; cost: string }
export interface TopRow { key: string | null; label: string | null; cost: string; split?: AccountCost[] }
export interface Top { dimension: string; rows: TopRow[]; total: string; groups: number; other: string; other_split?: AccountCost[] }
export interface MonthFreshness { month: string; built_at: string | null; rows: number; complete: boolean; incomplete_accounts?: string[] }
export interface ScopeQuality { synthetic: boolean; attributed_share: number | null; months: MonthFreshness[]; has_incomplete_month: boolean }
export interface Utilization { used_hours: string; unused_hours: string; ratio: number | null; used_cost: string; unused_cost: string; ratio_cost: number | null; definition: string }
export interface Coverage { covered: string; eligible: string; ratio: number | null; excluded_missing_price_rows: number; contract_basis_rows: number; definition: string }
export interface ObservedDiscount { payg_equivalent: string; benefit_cost: string; gross: string; unused_cost: string; net: string; definition: string }
export interface Commitments { available: boolean; reason: string | null; utilization: Utilization | null; coverage: Coverage | null; observed_discount: ObservedDiscount | null }
export interface AccountRow { account: string; currency: string; cost: string; rows: number; converted: string | null; partial: boolean; share: number | null;
  metrics_currency: string; total_metric: string; usage: string; unused: string | null; utilization: number | null;
  coverage: number | null; discount_net: string | null; attributed_share: number | null }
export interface AccountMonth { account: string; currency: string; month: string; cost: string }
export interface PayModels { overall: { model: string | null; cost: string }[]; by_account: { account: string; model: string | null; cost: string }[] }
export interface AccountSeriesPoint { period: string; account: string; total: string; usage: string }
export interface Overview { totals: Totals; by_account: AccountRow[]; by_account_month: AccountMonth[]; daily_by_account?: AccountSeriesPoint[]; pay_models?: PayModels; monthly_by_account?: AccountSeriesPoint[]; daily: SeriesPoint[]; monthly: SeriesPoint[]; top: Record<string, Top>; commitments: Commitments; quality: ScopeQuality }
export interface CommitmentMetrics {
  used_cost: string; savings_plan_cost: string; unused_reservation: string; unused_savings_plan: string; unused_total: string;
  utilization: number | null; covered: string; on_demand: string; coverage: number | null; benefit_cost: string;
  gross: string; net: string;
}
export interface CommitmentMonth extends CommitmentMetrics { month: string }
export interface CommitmentCategory extends CommitmentMetrics { category: string | null; eligible: boolean }
export interface CommitmentsDetail extends Commitments {
  spend: { used_cost: string; savings_plan_cost: string; unused_reservation: string; unused_savings_plan: string } | null;
  by_month: CommitmentMonth[]; by_category: CommitmentCategory[]; category_count: number;
}
export interface ReservationRow {
  commitment_id: string; name: string | null; meter_category: string | null; used_hours: string; unused_hours: string;
  used_cost: string; unused_cost: string; utilization: number | null;
}
export interface Reservations {
  available: boolean; reason: string | null; count: number; rows: ReservationRow[]; without_used_rows: number;
  total_unused_cost: string | null; filters_not_applied: string[]; grain: string | null;
}
export interface MovementRow {
  meter_id: string; unit: string; name: string | null; category: string | null;
  kind: "price_volume" | "new" | "removed" | "no_unit_price";
  quantity_from: string | null; quantity_to: string | null; cost_from: string; cost_to: string; change: string;
  volume: string | null; price: string | null;
}
export interface Movement {
  available: boolean; reason: string | null; month_from: string | null; month_to: string | null;
  change: string | null; volume: string | null; price: string | null; new: string | null; removed: string | null;
  no_unit_price: string | null; reconciles: boolean | null; groups: number; rows: MovementRow[]; other: string | null;
  filters_not_applied: string[]; definition: string | null;
}
export interface GapRow { key: string | null; unattributed: string; untagged: string; cost: string }
export interface Applications {
  total: string; attributed: string; unattributed: string; untagged: string; attributed_share: number | null;
  application_count: number; environment_none: string; environment_none_share: number | null;
  gap_by_service: GapRow[]; gap_groups: number; gap_other: string; definition: string;
}
export interface WorkloadCategory { category: string | null; total: string; months: string[] }
export interface WorkloadGroup {
  id: string; name: string; matches: string[]; total: string; months: string[];
  category_count: number; categories: WorkloadCategory[]; other: string;
}
export interface Workloads { months: string[]; total: string; outside_families: string; groups: WorkloadGroup[]; definition: string }
export interface PriceRow {
  key: string | null; cost: string; cost_with_payg: string; payg_equivalent: string; ratio_paid: number | null;
  known_share: number | null; missing_rows: number; contract_rows: number; rows: number;
}
export interface Pricing {
  totals: PriceRow; by_service: PriceRow[]; service_groups: number; by_pricing_model: PriceRow[];
  reference: { price_benchmarks: number; subscriptions_named: number; subscriptions_in_data: number }; definition: string;
}
export interface HeadroomAmounts {
  on_demand_cost: string; matched_cost: string; unmatched_cost: string; ambiguous_cost: string; no_sheet_cost: string;
  eligible_cost_1y: string; eligible_cost_3y: string; cheaper_cost_1y: string; cheaper_cost_3y: string;
  saving_1y: string; saving_3y: string;
}
export interface SpTerm { full_per_hour: string; recommended_per_hour: string; discount: number | null; saving_full: string; saving_recommended: string }
export interface SpRecommendation {
  available: boolean; reason: string | null; month: string; days: number; steady_factor: number | null;
  quiet_day_per_hour: string | null; average_day_per_hour: string | null; terms: Partial<Record<"1y" | "3y", SpTerm>>; definition: string;
}
export interface Headroom {
  available: boolean; month?: string | null; recommendation?: SpRecommendation | null; reason: string | null; price_months: string[]; filters_not_applied: string[];
  totals: (HeadroomAmounts & { matched_share: number | null }) | null;
  by_month: (HeadroomAmounts & { month: string })[];
  by_service: (HeadroomAmounts & { key: string | null })[]; service_groups: number;
  by_meter: (HeadroomAmounts & { meter_id: string; name: string | null; category: string | null })[]; meter_groups: number;
  definition: string | null;
}
export interface ExploreRow { keys: (string | null)[]; labels: (string | null)[]; cost: string; rows: number }
export interface Explore { group_by: string[]; page: number; page_size: number; pages: number; groups: number; total: string; rows: ExploreRow[] }
export interface DimensionValue { value: string | null; label: string | null; cost: string }
export interface DimensionValues { dimension: string; values: DimensionValue[] }

export interface BreakdownPart { meter_id: string; subcategory: string | null; region: string | null; cost: string; quantity: string | null }
export interface BreakdownRow {
  meter_id: string; unit: string; name: string | null; cost: string; on_demand_cost: string; quantity: string | null; share: number | null;
  variants: number; parts: BreakdownPart[];
}
export interface ServiceBreakdown {
  available: boolean; service: string; reason: string | null; total: string | null; groups: number; rows: BreakdownRow[];
  other: string | null; filters_not_applied: string[]; grain: string | null; definition: string | null;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/** Query string for the shared filters. `exclude` omits one dimension (used by that dimension's own picker). */
export function filterParams(f: Filters, exclude?: Dim): URLSearchParams {
  const p = new URLSearchParams();
  p.set("cost_kind", f.costKind);
  p.set("currency", f.currency);
  if (f.combined) p.set("combined", "1");
  if (f.start) p.set("start", f.start);
  if (f.end) p.set("end", f.end);
  for (const d of DIMS) {
    if (d === exclude) continue;
    for (const v of f.dims[d] ?? []) p.append(d, v);
  }
  return p;
}

export interface Timing { totalMs: number | null; dbMs: number | null }

function parseServerTiming(h: string | null): Timing {
  const pick = (name: string): number | null => {
    const m = h?.match(new RegExp(`${name};dur=([0-9.]+)`));
    return m && m[1] ? Number(m[1]) : null;
  };
  return { totalMs: pick("total"), dbMs: pick("db") };
}

async function failure(res: Response): Promise<ApiError> {
  let msg = `Request failed (${res.status})`;
  try {
    const body = (await res.json()) as { error?: string; detail?: unknown; hint?: string };
    if (body.error) msg = body.hint ? `${body.error}. ${body.hint}` : body.error;
    else if (body.detail) msg = `Invalid request: ${JSON.stringify(body.detail).slice(0, 300)}`;
  } catch { /* non-JSON error body */ }
  return new ApiError(msg, res.status);
}

export async function getJson<T>(path: string, params: URLSearchParams | null, signal: AbortSignal):
  Promise<{ data: T; timing: Timing }> {
  const qs = params && [...params].length ? `?${params.toString()}` : "";
  const id = begin("GET", path, qs.slice(1));
  try {
    const res = await fetch(`${path}${qs}`, { signal, headers: { Accept: "application/json" } });
    const timing = parseServerTiming(res.headers.get("Server-Timing"));
    if (!res.ok) { finish(id, "error", res.status, timing); throw await failure(res); }
    const data = (await res.json()) as T;
    finish(id, "ok", res.status, timing);
    return { data, timing };
  } catch (e) {
    finish(id, signal.aborted ? "cancelled" : "error", null, null);   // no-op if already finished above
    throw e;
  }
}

/** The only write-shaped call: a SELECT statement for the SQL workbench. The custom header is the CSRF guard. */
export async function postJson<T>(path: string, body: unknown, signal: AbortSignal): Promise<T> {
  const id = begin("POST", path, "");
  try {
    const res = await fetch(path, {
      method: "POST", signal,
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-AzFinOps": "1" },
      body: JSON.stringify(body),
    });
    const timing = parseServerTiming(res.headers.get("Server-Timing"));
    if (!res.ok) { finish(id, "error", res.status, timing); throw await failure(res); }
    const data = (await res.json()) as T;
    finish(id, "ok", res.status, timing);
    return data;
  } catch (e) {
    finish(id, signal.aborted ? "cancelled" : "error", null, null);
    throw e;
  }
}

// ---- Data Management ----
export interface ImportFile { file_name: string; sha256: string; bytes: number | null; rows: number | null; schema_id: string | null }
export interface ValidationSummary { severity: "error" | "warning" | "info"; code: string; count: number; message: string; sample: string | null }
export interface ImportBatch {
  batch_id: number; source_kind: string; cost_kind: string; synthetic: boolean; status: string;
  period_start: string | null; period_end: string | null; rows: number | null; rejected: number | null;
  created_at: string; finished_at: string | null; notes: string | null; files: ImportFile[]; validation: ValidationSummary[];
}
export interface PriceSheetFile { file_name: string; imported_at: string; bytes: number | null; rows_loaded: number | null; rows_rejected: number | null }
export interface Imports { batches: ImportBatch[]; total_batches: number; price_sheet_files: PriceSheetFile[] }
export type CheckStatus = "pass" | "warn" | "fail" | "info";
export interface QualityCheck { id: string; title: string; status: CheckStatus; detail: string; numbers: Record<string, string | null> }
export interface DataQuality { checks: QualityCheck[]; summary: Record<CheckStatus, number>; full_reconciliation: boolean }
export interface SchemaTable { name: string; kind: "table" | "view"; columns: { name: string; type: string }[] }
export interface SchemaListing { tables: SchemaTable[] }
export interface SqlResult { columns: string[]; types: string[]; rows: (string | number | boolean | null)[][]; truncated: boolean; limit: number; elapsed_ms: number }

// ---- Exchange rates ----
export interface FxQueryResult {
  from: string; to: string; rate: string; matched: number; min: string; max: string;
  queried_at: string; source: string; definition: string;
}
export interface FxRate {
  month: string; from: string; to: string; rate: string; method: "price-list" | "manual";
  source: string; matched: number | null; saved_at: string | null;
}

export interface CombinedRow {
  month: string; currency: string; native: string; rate: string | null; rate_month: string | null; converted: string | null;
  status: "target" | "converted" | "carried" | "missing";
}
export interface CombinedSummary {
  target: string; rows: CombinedRow[]; missing: [string, string][]; carried: [string, string, string][]; definition: string;
}

export interface MoverRow { key: string | null; label: string | null; cost_from: string; cost_to: string; change: string; share: number | null }
export interface Movers {
  available: boolean; reason: string | null; dimension: string; month_from: string | null; month_to: string | null;
  total_from: string | null; total_to: string | null; change: string | null; complete_from: boolean | null; complete_to: boolean | null;
  increases: MoverRow[]; decreases: MoverRow[]; definition: string;
}

export type ChangeKind = "decommissioned" | "downsized" | "upsized";
export interface ResourceChange {
  kind: ChangeKind; resource_id: string; resource_name: string | null; subscription_id: string | null; subscription: string | null;
  resource_group: string | null; account: string | null; service: string | null; sku_from: string | null; sku_to: string | null;
  cost_from: string; cost_to: string; rate_from: string; rate_to: string; saving: string; stopped_on: string | null;
}
export interface ResourceChanges {
  available: boolean; reason: string | null; month_from: string | null; month_to: string | null; days_to: number | null;
  complete_from: boolean | null; complete_to: boolean | null;
  totals: Record<ChangeKind, { count: number; saving: string }>; net_saving: string;
  by_account: { account: string | null; saving: string }[]; rows: ResourceChange[]; filters_not_applied: string[]; definition: string;
}

export interface SpHourly {
  available: boolean; reason: string | null; last_day: string | null;
  uncovered_last_day: string | null; uncovered_7d: string | null; uncovered_30d: string | null;
  baseline_30d: string | null; baseline_day: string | null; days_7d: number | null; days_30d: number | null;
  covered_7d: string | null; unused_7d: string | null;
  by_category_7d: { category: string; per_hour: string }[]; daily: { day: string; uncovered: string; covered: string }[];
  categories: string[]; categories_seen: string[]; definition: string;
}

export type ImportKind = "usage" | "pricesheet";
export interface DropFile { id: string; folder: string; name: string; bytes: number; modified: string; kind: ImportKind | null; imported: boolean | null }
export interface ImportFiles { folders: string[]; files: DropFile[] }
export interface ImportStatus {
  state: "idle" | "running" | "done" | "failed"; kind: ImportKind | null; files: string[]; step: string | null; current: string | null;
  rows_loaded: number; rows_rejected: number; started: string | null; finished: string | null; error: string | null;
  results: { files: string[]; summary: string; rows_loaded: number; rows_rejected: number }[]; log: string[];
  data_committed: boolean; recovery_required: boolean;
}

export interface Flow {
  available: boolean; reason: string | null; levels: Dim[]; total: string; drawn: string; credits: string;
  paths: { keys: (string | null)[]; value: string }[]; grouped: number[]; labels: Record<string, Record<string, string>>; definition: string;
}

export interface Analysis {
  available: boolean; reason: string | null; dimension: Dim; grain: "day" | "month"; periods: string[];
  series: { key: string | null; label: string | null; values: string[]; total: string }[]; totals: string[]; total: string;
  grouped: number; first_day: string | null; last_day: string | null;
  previous: { start: string; end: string; periods: string[]; totals: string[]; total: string } | null; definition: string;
  comparison_reason?: string | null;
}
export interface Budget { scope: string; currency: string; amount: string; updated: string | null }
export interface SavedView { name: string; query: string; saved: string | null }

export interface Summary {
  available: boolean; reason: string | null; total: string | null; first_day: string | null; last_day: string | null;
  days_with_data: number | null; billing_records: number | null; subscriptions: number | null; resources: number | null;
  latest_month: { month: string; cost: string; previous_month: string | null; previous_cost: string | null; change: number | null } | null;
  trends: { days: number; available: boolean; per_day: string | null; previous_per_day: string | null; change: number | null; days_needed: number }[];
  top_services: { service: string | null; cost: string; share: number | null }[]; top3_share: number | null; definition: string;
}
