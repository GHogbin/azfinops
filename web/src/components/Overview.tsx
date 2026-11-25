import { useMemo, useState } from "react";
import { filterParams, type Dim, type Filters, type Overview as OverviewData, type SeriesPoint } from "../api";
import { count, money, moneyShort, monthLabel, num, pct, periodLabel, previousMonth } from "../format";
import { useApi } from "../hooks";
import type { View } from "../state";
import { BarList } from "./BarList";
import { CostChart } from "./CostChart";
import { ErrorBox, TimingNote } from "./Status";
import { acctName, Dot, RatioSplit, SplitBar, StackTrack, type AmountItem } from "./EnrollmentSplit";
import { Kpi } from "./Kpi";
import { SummaryStrip } from "./SummaryStrip";
import { LazyTop } from "./LazyTop";
import { MoneyFlow } from "./MoneyFlow";
import { Opportunities } from "./Opportunities";
import { WhatChanged } from "./WhatChanged";
import { AttributionRing, PayBar } from "./BentoTiles";
import { HeroTrend } from "./HeroTrend";

interface Props {
  filters: Filters;
  onPick: (dim: Dim, value: string) => void;
  /** Switches to another workspace (used by the Opportunities card). */
  onOpen: (view: View) => void;
  /** Filters to a value and opens it in the Explorer, one level deeper. */
  onExplore: (dim: Dim, value: string) => void;
}

const TOPS: { dim: Dim; title: string }[] = [
  { dim: "subscription", title: "Top subscriptions" },
  { dim: "application", title: "Top applications" },
  { dim: "resource_group", title: "Top resource groups" },
];

type Acct = OverviewData["by_account"][number];

export function Overview({ filters, onPick, onOpen, onExplore }: Props) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  // Only the service list comes with the overview; the lists below the chart load when they scroll into view.
  const params = useMemo(() => { const p = filterParams(filters); p.set("tops", "service"); return p; }, [filters]);
  const { data, error, loading, timing, reload } = useApi<OverviewData>(ok ? "/api/overview" : null, ok ? params : null);
  // A single whole month is compared with the month before it, which is outside the range, so ask for it separately.
  const prevRange = ok ? previousMonth(filters.start, filters.end) : null;
  const prevParams = useMemo(() => {
    if (!prevRange) return null;
    const p = filterParams({ ...filters, start: prevRange.start, end: prevRange.end });
    p.set("grain", "month");
    return p;
  }, [filters, prevRange?.start]);
  const prevSeries = useApi<SeriesPoint[]>(prevParams ? "/api/cost/series" : null, prevParams);
  const [grain, setGrain] = useState<"day" | "month">("day");
  const [measure, setMeasure] = useState<"total" | "usage">("total");
  const [byEnr, setByEnr] = useState(true);
  const ccy = filters.currency;

  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <p className="empty" aria-live="polite">{ok ? "Loading overview…" : "Fix the date range to load data."}</p>;

  const { totals, quality: qual, commitments: c } = data;
  const incomplete = qual.months.filter((mm) => !mm.complete);
  const basis = filters.costKind === "amortized"
    ? "Amortized: reservation and savings-plan costs are spread over their term, and unused commitment shows as its own charge."
    : "Actual: charges as billed. Purchases appear in the month they are billed; unused commitment is not visible.";

  const m = data.monthly;
  const last = m[m.length - 1];
  const prev = m[m.length - 2];
  // The change compares two complete months: the last two in range, or the selected month and the one before it.
  const before: SeriesPoint | undefined = prevRange ? prevSeries.data?.[0] : prev;
  const delta = last?.complete && before?.complete && !loading && !prevSeries.loading && incomplete.length === 0
    ? num(last.total) - num(before.total) : null;
  const period = periodLabel(filters.start, filters.end, m.map((p) => p.period));
  const service = data.top.service;
  const accts = data.by_account;
  const amounts = (f: (a: Acct) => string | null): AmountItem[] =>
    accts.map((a) => {
      const v = f(a);
      return { account: a.account, value: v === null ? 0 : num(v), text: v === null ? "n/a" : moneyShort(v, a.metrics_currency), currency: a.metrics_currency };
    });
  const ratios = (f: (a: Acct) => number | null) => accts.map((a) => ({ account: a.account, value: f(a) }));
  const gap = qual.attributed_share === null ? "0" : String(num(totals.total) * (1 - qual.attributed_share));
  const heroTrack: AmountItem[] = filters.combined
    ? accts.map((a) => ({ account: a.account, value: num(a.converted), text: a.converted === null ? "left out" : moneyShort(a.converted, ccy), currency: ccy }))
    : [];

  return (
    <div className={loading ? "loading" : ""} aria-busy={loading}>
      <SummaryStrip filters={filters} />
      {qual.synthetic && <p className="banner warn" role="status"><strong>Synthetic demo data.</strong> These figures are generated and do not describe any real environment.</p>}
      {incomplete.length > 0 && (
        <p className="banner warn" role="status">
          <strong>Partial period.</strong> {incomplete.map((mm) =>
            `${monthLabel(mm.month)}${mm.incomplete_accounts?.length ? ` (${mm.incomplete_accounts.map(acctName).join(", ")})` : ""}`
          ).join(", ")} does not cover every day for every selected enrollment and currency, so its totals are incomplete.
        </p>
      )}

      <div className="bento2">
        <Kpi hero label={`Total cost${period ? ` · ${period}` : ""}${filters.combined ? ` · all enrollments combined in ${ccy}` : ""}`}
          note={`${money(totals.total, ccy, 2)} · ${delta === null ? `${count(totals.rows)} rows` : `${delta >= 0 ? "▲" : "▼"} ${moneyShort(Math.abs(delta), ccy)} (${pct(Math.abs(delta) / Math.max(1, Math.abs(num(before?.total))))}) ${prevRange ? "vs" : `${monthLabel(last?.period ?? "")} vs`} ${monthLabel(before?.period ?? "")}`}`}
          info="Signed sum of cost for the selected basis, currency, dates and filters. Credits and refunds are included as negatives. For a single month the change compares it with the month before; for a longer range, the last two months in it. It is hidden while any month is incomplete.">
          <HeroTrend points={data.daily} byAccount={data.daily_by_account} currency={ccy} />
          {accts.length > 0 && (
            <div className="hero-accts">
              <StackTrack items={heroTrack} label="Total cost by enrollment" />
              <ul className="by-acct" aria-label="Total cost by enrollment">
                {accts.map((a) => (
                  <li key={a.account}>
                    <span><Dot account={a.account} /> {acctName(a.account)}</span>
                    <span className="muted">{moneyShort(a.cost, a.currency)}{filters.combined && a.currency !== ccy ? " (own currency)" : ""}</span>
                    {filters.combined
                      ? <strong title={a.partial ? "Some months have no saved rate and are left out of the combined total" : undefined}>
                          {a.converted === null ? "" : moneyShort(a.converted, ccy)}{a.partial ? " *" : ""}</strong>
                      : <strong />}
                    <span className="muted">{a.share === null ? "" : pct(a.share)}</span>
                  </li>
                ))}
                {accts.some((a) => a.partial) && <li className="muted small">* part of this enrollment is left out of the combined total: no rate is saved for some months.</li>}
              </ul>
            </div>
          )}
          {new Set(accts.map((a) => a.currency)).size > 1 && !filters.combined && (
            <p className="muted small">These are in different currencies, so they are not added together. Choose a Combined option in Filters to add them up.</p>
          )}
        </Kpi>

        <div className="tiles-right">
          <section className="kpi pay-tile" aria-label="How usage is paid">
            <div className="kpi-label">How usage is paid</div>
            <PayBar filters={filters} data={data.pay_models} />
          </section>
          <section className="kpi wide commit-group" aria-label="Commitments">
            <div className="kpi-label">Commitments · reservations and savings plans</div>
            {c.available ? (
              <div className="commit-grid">
                <Kpi label="Unused" value={c.observed_discount ? moneyShort(c.observed_discount.unused_cost, ccy) : "n/a"} full={c.observed_discount ? money(c.observed_discount.unused_cost, ccy, 2) : undefined} note="paid for, not used"
                  info="Amortized cost of commitment capacity nobody used.">
                  <SplitBar label="Unused commitments by enrollment" items={amounts((a) => a.unused)} />
                </Kpi>
                <Kpi label="Utilization" value={c.utilization ? pct(c.utilization.ratio_cost) : "n/a"}
                  note={c.utilization ? `cost-weighted · hours ${pct(c.utilization.ratio)}` : "no reservations"} info={c.utilization?.definition}>
                  <RatioSplit label="Utilization by enrollment" items={ratios((a) => a.utilization)} />
                </Kpi>
                <Kpi label="Coverage" value={c.coverage ? pct(c.coverage.ratio) : "n/a"} note="of eligible usage, at contract price" info={c.coverage?.definition}>
                  <RatioSplit label="Coverage by enrollment" items={ratios((a) => a.coverage)} />
                </Kpi>
                <Kpi label="Observed discount" value={c.observed_discount ? moneyShort(c.observed_discount.net, ccy) : "n/a"} full={c.observed_discount ? money(c.observed_discount.net, ccy, 2) : undefined}
                  note={c.observed_discount ? `net · gross ${moneyShort(c.observed_discount.gross, ccy)}` : undefined} info={c.observed_discount?.definition}>
                  <SplitBar label="Observed discount by enrollment" items={amounts((a) => a.discount_net)} />
                </Kpi>
              </div>
            ) : <p className="muted small">{c.reason ?? "Commitment figures need the amortized basis."}</p>}
          </section>
        </div>

        <WhatChanged filters={filters} onPick={onPick} />
        <Opportunities c={c} filters={filters} onOpen={() => onOpen("commitments")} />

        <div className="tile-services">
          {service ? <BarList title="Top services (meter category)" rows={service.rows.slice(0, 5)} other={service.other} otherSplit={service.other_split} total={service.total}
            groups={service.groups} currency={ccy} onPick={(v) => onPick("service", v)} onOpen={(v) => onExplore("service", v)} /> : null}
        </div>
        <section className="card tile-ring" aria-label="Attribution">
          <AttributionRing share={qual.attributed_share} gap={gap} currency={ccy}
            accounts={accts.map((a) => ({ account: a.account, share: a.attributed_share }))} />
        </section>
      </div>

      <div className="chart-controls" role="group" aria-label="Chart options">
        <div className="seg" role="group" aria-label="Grain">
          {(["day", "month"] as const).map((g) => (
            <button key={g} type="button" aria-pressed={grain === g} onClick={() => setGrain(g)}>{g === "day" ? "Daily" : "Monthly"}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Measure">
          {(["total", "usage"] as const).map((x) => (
            <button key={x} type="button" aria-pressed={measure === x} onClick={() => setMeasure(x)}>{x === "total" ? "Total" : "Usage only"}</button>
          ))}
        </div>
        {accts.length > 1 && (
          <div className="seg" role="group" aria-label="Split">
            <button type="button" aria-pressed={!byEnr} onClick={() => setByEnr(false)}>One total</button>
            <button type="button" aria-pressed={byEnr} onClick={() => setByEnr(true)}>By enrollment</button>
          </div>
        )}
      </div>
      <CostChart points={grain === "day" ? data.daily : data.monthly} currency={ccy} grain={grain} measure={measure}
        byAccount={grain === "day" ? data.daily_by_account : data.monthly_by_account} stacked={byEnr}
        title={`${grain === "day" ? "Daily" : "Monthly"} ${measure === "total" ? "total" : "usage"} cost (${ccy})`} />

      <MoneyFlow filters={filters} />

      <div className="grid-2">
        {TOPS.map(({ dim, title }) => (
          <LazyTop key={dim} dim={dim} title={title} filters={filters} onPick={(v) => onPick(dim, v)} onOpen={(v) => onExplore(dim, v)} />
        ))}
      </div>

      {c.available && c.coverage && (c.coverage.contract_basis_rows > 0 || c.coverage.excluded_missing_price_rows > 0) && (
        <p className="muted small">
          Coverage note: covered and eligible usage are both valued at the contract unit price.
          {c.coverage.contract_basis_rows > 0 && ` ${count(c.coverage.contract_basis_rows)} benefit rows use it as their baseline.`}
          {c.coverage.excluded_missing_price_rows > 0 && ` ${count(c.coverage.excluded_missing_price_rows)} rows have no price recorded and are left out, not counted as zero.`}
          {" "}Eligibility is inferred from the data, not from a published list.
        </p>
      )}
      <p className="muted small">{basis} {filters.combined ? `Values are converted into ${ccy} with the rates saved for each month (see the banner above); each enrollment’s own currency is shown in the Total cost tile.` : `Values are in ${ccy}, the billing currency of the export; nothing is converted.`}{" "}
        Cost per enrollment per month is on the Spend tab; row counts and data checks are in Data Management.</p>
      <TimingNote timing={timing} loading={loading} bytesHint={num(totals.rows)} />
    </div>
  );
}
