"""Response contracts. Money and quantities are serialised as decimal STRINGS (exact, 4 dp);
ratios are floats. Clients round for display; nothing is summed from rounded numbers server-side."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer

_Q = Decimal("0.0001")


def _money(v: Decimal) -> str:
    return format(v.quantize(_Q), "f")


def _ratio(v: Decimal | None) -> float | None:
    return None if v is None else round(float(v), 6)


Money = Annotated[Decimal, PlainSerializer(_money, return_type=str, when_used="json")]
Ratio = Annotated[Decimal | None, PlainSerializer(_ratio, return_type=float | None, when_used="json")]


class Health(BaseModel):
    status: str
    version: str


class Totals(BaseModel):
    total: Money
    usage: Money
    rows: int
    by_charge_type: dict[str, Money]


class SeriesPoint(BaseModel):
    period: date
    total: Money
    usage: Money
    complete: bool | None = None


class AccountCost(BaseModel):
    account: str
    cost: Money


class TopRow(BaseModel):
    key: str | None
    label: str | None = None
    cost: Money
    split: list[AccountCost] = []


class Top(BaseModel):
    dimension: str
    rows: list[TopRow]
    total: Money
    groups: int
    other: Money
    other_split: list[AccountCost] = []


class MonthFreshness(BaseModel):
    month: date
    built_at: datetime | None
    rows: int
    complete: bool
    incomplete_accounts: list[str] = []


class ScopeQuality(BaseModel):
    synthetic: bool
    attributed_share: Ratio
    months: list[MonthFreshness]
    has_incomplete_month: bool


class Utilization(BaseModel):
    used_hours: Money
    unused_hours: Money
    ratio: Ratio
    used_cost: Money
    unused_cost: Money
    ratio_cost: Ratio
    definition: str


class Coverage(BaseModel):
    covered: Money
    eligible: Money
    ratio: Ratio
    excluded_missing_price_rows: int
    contract_basis_rows: int
    definition: str


class ObservedDiscount(BaseModel):
    payg_equivalent: Money
    benefit_cost: Money
    gross: Money
    unused_cost: Money
    net: Money
    definition: str


class Commitments(BaseModel):
    available: bool
    reason: str | None = None
    utilization: Utilization | None = None
    coverage: Coverage | None = None
    observed_discount: ObservedDiscount | None = None


class CommitmentSpend(BaseModel):
    used_cost: Money
    savings_plan_cost: Money
    unused_reservation: Money
    unused_savings_plan: Money


class CommitmentMetrics(BaseModel):
    used_cost: Money
    savings_plan_cost: Money
    unused_reservation: Money
    unused_savings_plan: Money
    unused_total: Money
    utilization: Ratio
    covered: Money
    on_demand: Money
    coverage: Ratio
    benefit_cost: Money
    gross: Money
    net: Money


class CommitmentMonth(CommitmentMetrics):
    month: date


class CommitmentCategory(CommitmentMetrics):
    category: str | None
    eligible: bool


class CommitmentsDetail(Commitments):
    spend: CommitmentSpend | None = None
    by_month: list[CommitmentMonth] = []
    by_category: list[CommitmentCategory] = []
    category_count: int = 0


class MovementRow(BaseModel):
    meter_id: str
    unit: str
    name: str | None
    category: str | None
    kind: str
    quantity_from: Money | None
    quantity_to: Money | None
    cost_from: Money
    cost_to: Money
    change: Money
    volume: Money | None
    price: Money | None


class Movement(BaseModel):
    available: bool
    reason: str | None = None
    month_from: date | None = None
    month_to: date | None = None
    change: Money | None = None
    volume: Money | None = None
    price: Money | None = None
    new: Money | None = None
    removed: Money | None = None
    no_unit_price: Money | None = None
    reconciles: bool | None = None
    groups: int = 0
    rows: list[MovementRow] = []
    other: Money | None = None
    filters_not_applied: list[str] = []
    definition: str | None = None


class GapRow(BaseModel):
    key: str | None
    unattributed: Money
    untagged: Money
    cost: Money


class Applications(BaseModel):
    total: Money
    attributed: Money
    unattributed: Money
    untagged: Money
    attributed_share: Ratio | None
    application_count: int
    environment_none: Money
    environment_none_share: Ratio | None
    gap_by_service: list[GapRow]
    gap_groups: int
    gap_other: Money
    definition: str


class WorkloadCategory(BaseModel):
    category: str | None
    total: Money
    months: list[Money]


class WorkloadGroup(BaseModel):
    id: str
    name: str
    matches: list[str]
    total: Money
    months: list[Money]
    category_count: int
    categories: list[WorkloadCategory]
    other: Money


class Workloads(BaseModel):
    months: list[date]
    total: Money
    outside_families: Money
    groups: list[WorkloadGroup]
    definition: str


class PriceRow(BaseModel):
    key: str | None
    cost: Money
    cost_with_payg: Money
    payg_equivalent: Money
    ratio_paid: Ratio
    known_share: Ratio
    missing_rows: int
    contract_rows: int
    rows: int


class ReferenceStatus(BaseModel):
    price_benchmarks: int
    subscriptions_named: int
    subscriptions_in_data: int


class Pricing(BaseModel):
    totals: PriceRow
    by_service: list[PriceRow]
    service_groups: int
    by_pricing_model: list[PriceRow]
    reference: ReferenceStatus
    definition: str


class HeadroomAmounts(BaseModel):
    on_demand_cost: Money
    matched_cost: Money
    unmatched_cost: Money
    ambiguous_cost: Money
    no_sheet_cost: Money
    eligible_cost_1y: Money
    eligible_cost_3y: Money
    cheaper_cost_1y: Money
    cheaper_cost_3y: Money
    saving_1y: Money
    saving_3y: Money


class HeadroomTotals(HeadroomAmounts):
    matched_share: Ratio


class HeadroomMonth(HeadroomAmounts):
    month: date


class HeadroomService(HeadroomAmounts):
    key: str | None


class HeadroomMeter(HeadroomAmounts):
    meter_id: str
    name: str | None
    category: str | None


class SpTerm(BaseModel):
    full_per_hour: Money
    recommended_per_hour: Money
    discount: Ratio
    saving_full: Money
    saving_recommended: Money


class SpRecommendation(BaseModel):
    available: bool
    reason: str | None = None
    month: date
    days: int
    steady_factor: Ratio = None
    quiet_day_per_hour: Money | None = None
    average_day_per_hour: Money | None = None
    terms: dict[str, SpTerm]
    definition: str


class Headroom(BaseModel):
    available: bool
    reason: str | None
    month: date | None = None
    price_months: list[date]
    filters_not_applied: list[str]
    totals: HeadroomTotals | None
    recommendation: SpRecommendation | None = None
    by_month: list[HeadroomMonth]
    by_service: list[HeadroomService]
    service_groups: int
    by_meter: list[HeadroomMeter]
    meter_groups: int
    definition: str | None


class AccountRow(BaseModel):
    account: str
    currency: str
    cost: Money
    rows: int
    converted: Money | None = None
    partial: bool = False
    share: float | None
    metrics_currency: str
    total_metric: Money
    usage: Money
    unused: Money | None = None
    utilization: float | None = None
    coverage: float | None = None
    discount_net: Money | None = None
    attributed_share: float | None = None


class PayModel(BaseModel):
    model: str | None
    cost: Money


class PayModelAccount(PayModel):
    account: str


class PayModels(BaseModel):
    overall: list[PayModel]
    by_account: list[PayModelAccount]


class AccountSeriesPoint(BaseModel):
    period: date
    account: str
    total: Money
    usage: Money


class AccountMonth(BaseModel):
    account: str
    currency: str
    month: date
    cost: Money


class Overview(BaseModel):
    totals: Totals
    by_account: list[AccountRow]
    by_account_month: list[AccountMonth]
    daily_by_account: list[AccountSeriesPoint] = []
    pay_models: PayModels | None = None
    monthly_by_account: list[AccountSeriesPoint] = []
    daily: list[SeriesPoint]
    monthly: list[SeriesPoint]
    top: dict[str, Top]
    commitments: Commitments
    quality: ScopeQuality


class ExploreRow(BaseModel):
    keys: list[str | None]
    labels: list[str | None]
    cost: Money
    rows: int


class Explore(BaseModel):
    group_by: list[str]
    page: int
    page_size: int
    pages: int
    groups: int
    total: Money
    rows: list[ExploreRow]


class DimensionValue(BaseModel):
    value: str | None
    label: str | None = None
    cost: Money


class DimensionValues(BaseModel):
    dimension: str
    values: list[DimensionValue]


class ReservationRow(BaseModel):
    commitment_id: str
    name: str | None
    meter_category: str | None
    used_hours: Money
    unused_hours: Money
    used_cost: Money
    unused_cost: Money
    utilization: Ratio


class Reservations(BaseModel):
    available: bool
    reason: str | None = None
    count: int = 0
    rows: list[ReservationRow] = []
    without_used_rows: int = 0
    total_unused_cost: Money | None = None
    filters_not_applied: list[str] = []
    grain: str | None = None


class Scope(BaseModel):
    cost_kind: str
    currency: str
    first_date: date
    last_date: date
    rows: int
    synthetic: bool


class ScopeMonth(BaseModel):
    month: date
    cost_kind: str
    account: str
    currency: str
    complete: bool


class Meta(BaseModel):
    scopes: list[Scope]
    billing_accounts: list[str]
    dimensions: list[str]
    billing_account_names: dict[str, str] = {}
    billing_account_currencies: dict[str, list[str]] = {}
    recovery_required: bool = False
    recovery_message: str | None = None
    scope_months: list[ScopeMonth] = []


# ---- Data Management -------------------------------------------------------------------------------

class ImportHistoryFile(BaseModel):
    file_name: str
    sha256: str
    bytes: int | None = None
    rows: int | None = None
    schema_id: str | None = None


class ValidationSummary(BaseModel):
    severity: str
    code: str
    count: int
    message: str
    sample: str | None = None


class ImportBatch(BaseModel):
    batch_id: int
    source_kind: str
    cost_kind: str
    synthetic: bool
    status: str
    period_start: date | None = None
    period_end: date | None = None
    rows: int | None = None
    rejected: int | None = None
    created_at: datetime
    finished_at: datetime | None = None
    notes: str | None = None
    files: list[ImportHistoryFile]
    validation: list[ValidationSummary]


class PriceSheetFile(BaseModel):
    file_name: str
    imported_at: datetime
    bytes: int | None = None
    rows_loaded: int | None = None
    rows_rejected: int | None = None


class Imports(BaseModel):
    batches: list[ImportBatch]
    total_batches: int
    price_sheet_files: list[PriceSheetFile]


class QualityCheck(BaseModel):
    id: str
    title: str
    status: str          # pass | warn | fail | info
    detail: str
    numbers: dict[str, str | None] = {}


class DataQuality(BaseModel):
    checks: list[QualityCheck]
    summary: dict[str, int]
    full_reconciliation: bool


class SchemaColumn(BaseModel):
    name: str
    type: str


class SchemaTable(BaseModel):
    name: str
    kind: str
    columns: list[SchemaColumn]


class SchemaListing(BaseModel):
    tables: list[SchemaTable]


class SqlRequest(BaseModel):
    sql: str
    limit: int = 1000


class SqlResult(BaseModel):
    columns: list[str]
    types: list[str]
    rows: list[list[object]]
    truncated: bool
    limit: int
    elapsed_ms: float


class BreakdownPart(BaseModel):
    meter_id: str
    subcategory: str | None
    region: str | None
    cost: Money
    quantity: Money | None


class BreakdownRow(BaseModel):
    meter_id: str
    unit: str
    name: str | None
    cost: Money
    on_demand_cost: Money
    quantity: Money | None
    share: Ratio
    variants: int = 1
    parts: list[BreakdownPart] = []


class ServiceBreakdown(BaseModel):
    available: bool
    service: str
    reason: str | None = None
    total: Money | None = None
    groups: int = 0
    rows: list[BreakdownRow] = []
    other: Money | None = None
    filters_not_applied: list[str] = []
    grain: str | None = None
    definition: str | None = None


class FxQuery(BaseModel):
    from_currency: str = "USD"
    to_currency: str = "GBP"


class FxQueryResult(BaseModel):
    model_config = {"populate_by_name": True}
    from_: str = Field(alias="from")
    to: str
    rate: str
    matched: int
    min: str
    max: str
    queried_at: str
    source: str
    definition: str


class FxSave(BaseModel):
    month: str
    from_currency: str
    to_currency: str
    rate: str
    method: str
    source: str = ""
    matched: int | None = None


class FxRate(BaseModel):
    model_config = {"populate_by_name": True}
    month: str
    from_: str = Field(alias="from")
    to: str
    rate: str
    method: str
    source: str = ""
    matched: int | None = None
    saved_at: str | None = None


class FxRates(BaseModel):
    rates: list[FxRate]


class MoverRow(BaseModel):
    key: str | None
    label: str | None = None
    cost_from: Money
    cost_to: Money
    change: Money
    share: float | None


class Movers(BaseModel):
    available: bool
    reason: str | None = None
    dimension: str
    month_from: date | None = None
    month_to: date | None = None
    total_from: Money | None = None
    total_to: Money | None = None
    change: Money | None = None
    complete_from: bool | None = None
    complete_to: bool | None = None
    increases: list[MoverRow]
    decreases: list[MoverRow]
    definition: str


class ResourceChange(BaseModel):
    kind: str
    resource_id: str
    resource_name: str | None
    subscription_id: str | None
    subscription: str | None = None
    resource_group: str | None
    account: str | None
    service: str | None
    sku_from: str | None
    sku_to: str | None
    cost_from: Money
    cost_to: Money
    rate_from: Money
    rate_to: Money
    saving: Money
    stopped_on: date | None


class ChangeTotal(BaseModel):
    count: int
    saving: Money


class AccountSaving(BaseModel):
    account: str | None
    saving: Money


class ResourceChanges(BaseModel):
    available: bool
    reason: str | None = None
    month_from: date | None = None
    month_to: date | None = None
    days_to: int | None = None
    complete_from: bool | None = None
    complete_to: bool | None = None
    totals: dict[str, ChangeTotal]
    net_saving: Money
    by_account: list[AccountSaving]
    rows: list[ResourceChange]
    filters_not_applied: list[str]
    definition: str


class SpCategory(BaseModel):
    category: str
    per_hour: Money


class SpDay(BaseModel):
    day: date
    uncovered: Money
    covered: Money


class SpHourly(BaseModel):
    available: bool
    reason: str | None = None
    last_day: date | None = None
    uncovered_last_day: Money | None = None
    uncovered_7d: Money | None = None
    uncovered_30d: Money | None = None
    baseline_30d: Money | None = None
    baseline_day: date | None = None
    days_7d: int | None = None
    days_30d: int | None = None
    covered_7d: Money | None = None
    unused_7d: Money | None = None
    by_category_7d: list[SpCategory] = []
    daily: list[SpDay] = []
    categories: list[str]
    categories_seen: list[str]
    definition: str


class ImportFile(BaseModel):
    id: str
    folder: str
    name: str
    bytes: int
    modified: datetime
    kind: str | None
    imported: bool | None


class ImportFiles(BaseModel):
    folders: list[str]
    files: list[ImportFile]


class ImportStart(BaseModel):
    kind: Literal["usage", "pricesheet"]
    files: list[str] = Field(min_length=1, max_length=200)
    cost_kind: Literal["amortized", "actual"] = "amortized"
    supersede: bool = False
    reimport: bool = False


class ImportResultLine(BaseModel):
    files: list[str]
    summary: str
    rows_loaded: int
    rows_rejected: int


class ImportStatus(BaseModel):
    state: Literal["idle", "running", "done", "failed"]
    kind: str | None = None
    files: list[str] = []
    step: str | None = None
    current: str | None = None
    rows_loaded: int = 0
    rows_rejected: int = 0
    started: datetime | None = None
    finished: datetime | None = None
    error: str | None = None
    results: list[ImportResultLine] = []
    log: list[str] = []
    data_committed: bool = False
    recovery_required: bool = False


class FlowPath(BaseModel):
    keys: list[str | None]
    value: Money


class Flow(BaseModel):
    available: bool
    reason: str | None = None
    levels: list[str]
    total: Money
    drawn: Money
    credits: Money
    paths: list[FlowPath]
    grouped: list[int]
    labels: dict[str, dict[str, str]] = {}
    definition: str


class AnalysisSeries(BaseModel):
    key: str | None
    label: str | None = None
    values: list[Money]
    total: Money


class AnalysisPrevious(BaseModel):
    start: date
    end: date
    periods: list[date]
    totals: list[Money]
    total: Money


class Analysis(BaseModel):
    available: bool
    reason: str | None = None
    dimension: str
    grain: str
    periods: list[date]
    series: list[AnalysisSeries]
    totals: list[Money]
    total: Money
    grouped: int
    first_day: date | None
    last_day: date | None
    previous: AnalysisPrevious | None = None
    comparison_reason: str | None = None
    definition: str


class Budget(BaseModel):
    scope: str
    currency: str
    amount: str
    updated: str | None = None


class Budgets(BaseModel):
    budgets: list[Budget]


class BudgetSave(BaseModel):
    scope: str = Field(min_length=1, max_length=40)
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    amount: str | None = Field(default=None, max_length=20)


class SavedView(BaseModel):
    name: str
    query: str
    saved: str | None = None


class SavedViews(BaseModel):
    views: list[SavedView]


class ViewSave(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    query: str | None = Field(default=None, max_length=2000)


class LatestMonth(BaseModel):
    month: date
    cost: Money
    previous_month: date | None
    previous_cost: Money | None
    change: float | None


class Trend(BaseModel):
    days: int
    available: bool
    per_day: Money | None
    previous_per_day: Money | None
    change: float | None
    days_needed: int


class TopService(BaseModel):
    service: str | None
    cost: Money
    share: float | None


class Summary(BaseModel):
    available: bool
    reason: str | None = None
    total: Money | None = None
    first_day: date | None = None
    last_day: date | None = None
    days_with_data: int | None = None
    billing_records: int | None = None
    subscriptions: int | None = None
    resources: int | None = None
    latest_month: LatestMonth | None = None
    trends: list[Trend] = []
    top_services: list[TopService] = []
    top3_share: float | None = None
    definition: str
