import { useMemo } from "react";
import { filterParams, type Filters, type Headroom, type Meta } from "../api";
import { count, money, monthLabel, num, pct, moneyShort } from "../format";
import { useApi } from "../hooks";
import { acctName, Dot } from "./EnrollmentSplit";
import { Kpi } from "./Kpi";
import { SortTable, type Col } from "./SortTable";
import { ErrorBox, TimingNote } from "./Status";

/**
 * Savings-plan headroom, one block per currency: each enrollment is priced against its own currency's price sheet,
 * so in the Combined view every enrollment gets its own block in its own currency instead of no figure at all.
 */
export function SavingsHeadroom({ filters }: { filters: Filters }) {
  const meta = useApi<Meta>("/api/meta", null);
  const m = meta.data;
  if (!m) return null;
  const byCcy = new Map<string, string[]>();
  for (const [acct, ccys] of Object.entries(m.billing_account_currencies ?? {})) {
    for (const c of ccys) byCcy.set(c, [...(byCcy.get(c) ?? []), acct]);
  }
  const have = new Set(m.scopes.filter((x) => x.cost_kind === filters.costKind).map((x) => x.currency));
  const currencies = filters.combined ? [...have].sort() : [filters.currency];
  return (
    <>
      {currencies.map((c) => (
        <HeadroomBlock key={c} filters={{ ...filters, combined: false, currency: c }} accounts={byCcy.get(c) ?? []}
          several={byCcy.size > 1} />
      ))}
    </>
  );
}

/** Headroom for one currency (the enrollments billed in it). */
function HeadroomBlock({ filters, accounts, several }: { filters: Filters; accounts: string[]; several: boolean }) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const hparams = useMemo(() => { const p = filterParams(filters); p.set("n", "15"); return p; }, [filters]);
  const head = useApi<Headroom>(ok ? "/api/savings-headroom" : null, ok ? hparams : null);
  const ccy = filters.currency;
  const h = head.data;
  const t = h?.totals;
  const hCols = [
    { key: "svc", header: "Service", sortValue: (r: HeadRow) => r.key ?? "", render: (r: HeadRow) => r.key ?? "(none)" },
    { key: "od", header: "On-demand cost", num: true, sortValue: (r: HeadRow) => num(r.on_demand_cost), render: (r: HeadRow) => money(r.on_demand_cost, ccy) },
    { key: "m", header: "Matched", num: true, info: "Cost matched to a price sheet price.", sortValue: (r: HeadRow) => num(r.matched_cost), render: (r: HeadRow) => money(r.matched_cost, ccy) },
    { key: "e3", header: "Has a plan price", num: true, info: "Matched cost whose meter has a 3-year savings-plan price in the sheet.", sortValue: (r: HeadRow) => num(r.eligible_cost_3y), render: (r: HeadRow) => money(r.eligible_cost_3y, ccy) },
    { key: "c3", header: "Plan cheaper (3y)", num: true, info: "Part of that cost where the 3-year plan price is below your on-demand contract price. Elsewhere the ceiling is zero.", sortValue: (r: HeadRow) => num(r.cheaper_cost_3y), render: (r: HeadRow) => money(r.cheaper_cost_3y, ccy) },
    { key: "s1", header: "Ceiling, 1 year", num: true, sortValue: (r: HeadRow) => num(r.saving_1y), render: (r: HeadRow) => money(r.saving_1y, ccy) },
    { key: "s3", header: "Ceiling, 3 years", num: true, sortValue: (r: HeadRow) => num(r.saving_3y), render: (r: HeadRow) => <strong>{money(r.saving_3y, ccy)}</strong> },
  ] satisfies Col<HeadRow>[];
  const mCols = [
    { key: "name", header: "Meter", sortValue: (r: MeterRow) => r.name ?? r.meter_id,
      render: (r: MeterRow) => <><span>{r.name ?? r.meter_id}</span><div className="muted small">{r.category ?? "—"} · {r.meter_id.slice(0, 8)}</div></> },
    { key: "od", header: "On-demand cost", num: true, sortValue: (r: MeterRow) => num(r.on_demand_cost), render: (r: MeterRow) => money(r.on_demand_cost, ccy) },
    { key: "s1", header: "Ceiling, 1 year", num: true, sortValue: (r: MeterRow) => num(r.saving_1y), render: (r: MeterRow) => money(r.saving_1y, ccy) },
    { key: "s3", header: "Ceiling, 3 years", num: true, sortValue: (r: MeterRow) => num(r.saving_3y), render: (r: MeterRow) => <strong>{money(r.saving_3y, ccy)}</strong> },
  ] satisfies Col<MeterRow>[];

  return (
    <>
      <section className="card" aria-label="Savings plan headroom">
        <header className="card-head"><h3>Savings-plan headroom (upper bound){h?.month ? ` · ${monthLabel(h.month)}` : ""}{several ? ` · ${ccy}` : ""}</h3>
          {several && accounts.length > 0 && (
            <span className="muted">{accounts.map((a) => <span key={a} className="hr-acct"><Dot account={a} /> {acctName(a)}</span>)}</span>
          )}</header>
        {head.error && <ErrorBox message={head.error} onRetry={head.reload} />}
        {!h && !head.error && <p className="empty" aria-live="polite">Loading…</p>}
        {h && !h.available && <p className="banner warn" role="status">{h.reason}</p>}
        {h && h.available && t && (
          <div className={head.loading ? "loading" : ""} aria-busy={head.loading}>
            <p className="banner warn" role="status">
              {h.recommendation?.reason ?? "Monthly savings are ceilings, not hourly purchase recommendations. Hourly sizing needs hourly eligible usage and existing commitment coverage."}
            </p>
            <div className="kpis">
              <Kpi label="On-demand usage" value={moneyShort(t.on_demand_cost, ccy)} full={money(t.on_demand_cost, ccy)} note="in this selection" />
              <Kpi label="Matched to price sheet" value={pct(t.matched_share)} note={money(t.matched_cost, ccy)} info="Share of on-demand cost that could be matched to a price sheet price by meter, offer and unit." />
              <Kpi label="Has a plan price" value={moneyShort(t.eligible_cost_3y, ccy)} full={money(t.eligible_cost_3y, ccy)} note="3-year price in the sheet" info="Matched on-demand cost whose meter has a savings-plan price at all." />
              <Kpi label="Plan cheaper (3y)" value={moneyShort(t.cheaper_cost_3y, ccy)} full={money(t.cheaper_cost_3y, ccy)} note="below your contract price" info="Part of that cost where the 3-year plan price is below your on-demand contract price. Your contract already includes negotiated discounts, so many plans are not cheaper, and the 1-year plan is often barely below." />
              <Kpi label="Ceiling, 1 year" value={moneyShort(t.saving_1y, ccy)} full={money(t.saving_1y, ccy)} note="not a forecast" info={h.definition ?? undefined} />
              <Kpi label="Ceiling, 3 years" value={moneyShort(t.saving_3y, ccy)} full={money(t.saving_3y, ccy)} note="not a forecast" info={h.definition ?? undefined} />
            </div>
            <p className="banner warn" role="note">
              This is a ceiling, not a recommendation. It assumes every eligible hour is covered at the sheet price. A savings plan is sized on
              a steady hourly baseline and this export is daily, so real savings are lower.
            </p>
            {(num(t.unmatched_cost) + num(t.ambiguous_cost) + num(t.no_sheet_cost)) > 0 && (
              <p className="muted small">
                Not priced: {money(t.unmatched_cost, ccy)} did not match a price sheet row, {money(t.ambiguous_cost, ccy)} had several or no usable
                prices, and {money(t.no_sheet_cost, ccy)} falls in a month without a price sheet. These get no saving.
              </p>
            )}
            <SortTable caption="Savings-plan ceiling by service" columns={hCols} rows={h.by_service} rowKey={(r) => r.key ?? "(none)"}
              initialSort={{ key: "s3", desc: true }} />
            {h.service_groups > h.by_service.length && <p className="muted small">Showing the top {h.by_service.length} of {count(h.service_groups)} services.</p>}
            <SortTable caption="Savings-plan ceiling by meter" columns={mCols} rows={h.by_meter} rowKey={(r) => r.meter_id}
              initialSort={{ key: "s3", desc: true }} />
            <p className="muted small">
              Latest month only{h.month ? ` (${monthLabel(h.month)})` : ""}: usage that no reservation or savings plan covered in the newest month of the
              selected period, priced against that month’s price sheet.{h.month && !h.price_months.includes(h.month) && " No price sheet is loaded for that month, so nothing can be priced."}
              {" "}Price sheets loaded for: {h.price_months.map((m) => monthLabel(m)).join(", ")}. Reservation prices are not compared.
              {h.filters_not_applied.length > 0 && ` Not applied at this grain: ${h.filters_not_applied.join(", ")}.`}
            </p>
          </div>
        )}
      </section>
      <TimingNote timing={head.timing} loading={head.loading} />
    </>
  );
}

type HeadRow = NonNullable<Headroom["by_service"][number]>;
type MeterRow = NonNullable<Headroom["by_meter"][number]>;
