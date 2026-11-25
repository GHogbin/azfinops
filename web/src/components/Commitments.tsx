import { useMemo } from "react";
import {
  filterParams, type CommitmentCategory, type CommitmentMonth, type CommitmentsDetail, type Filters,
  type ReservationRow, type Reservations,
} from "../api";
import { count, money, monthLabel, num, pct, moneyShort } from "../format";
import { useApi } from "../hooks";
import { Kpi } from "./Kpi";
import { SortTable, type Col } from "./SortTable";
import { SavingsHeadroom } from "./SavingsHeadroom";
import { SpHourly } from "./SpHourly";
import { ErrorBox, TimingNote } from "./Status";

interface Props { filters: Filters }

function UtilBar({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">n/a</span>;
  return (
    <span className="util">
      <span className="bar-track" aria-hidden="true"><span className="bar-fill" style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} /></span>
      <span>{pct(value)}</span>
    </span>
  );
}

export function Commitments({ filters }: Props) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const amortized = filters.costKind === "amortized";
  const params = useMemo(() => filterParams(filters), [filters]);
  const rparams = useMemo(() => { const p = filterParams(filters); p.set("n", "15"); return p; }, [filters]);
  const det = useApi<CommitmentsDetail>(ok ? "/api/commitments" : null, ok ? params : null);
  const res = useApi<Reservations>(ok && amortized ? "/api/reservations" : null, ok && amortized ? rparams : null);
  const ccy = filters.currency;

  if (det.error) return <ErrorBox message={det.error} onRetry={det.reload} />;
  if (!det.data) return <p className="empty" aria-live="polite">{ok ? "Loading commitments…" : "Fix the date range to load data."}</p>;
  const d = det.data;
  if (!d.available) {
    return (
      <div>
        <p className="banner warn" role="status"><strong>Needs the amortized basis.</strong> {d.reason} Switch “Cost basis and currency” to Amortized cost.</p>
        <SpHourly filters={filters} />
        <SavingsHeadroom filters={filters} />
      </div>
    );
  }
  const u = d.utilization;
  const cov = d.coverage;
  const disc = d.observed_discount;
  const spend = d.spend;
  const unusedTotal = spend ? num(spend.unused_reservation) + num(spend.unused_savings_plan) : 0;

  const catCols: Col<CommitmentCategory>[] = [
    { key: "category", header: "Meter category", sortValue: (r) => r.category ?? "",
      render: (r) => <>{r.category ?? "(none)"}{!r.eligible && <span className="muted small"> · no benefit usage</span>}</> },
    { key: "spend", header: "Benefit spend", num: true, info: "Amortized cost of usage covered by reservations and savings plans.",
      sortValue: (r) => num(r.used_cost) + num(r.savings_plan_cost), render: (r) => money(num(r.used_cost) + num(r.savings_plan_cost), ccy) },
    { key: "util", header: "Reservation utilization", num: true, info: "Used / (used + unused) amortized cost of reservations.",
      sortValue: (r) => r.utilization, render: (r) => <UtilBar value={r.utilization} /> },
    { key: "unused", header: "Unused cost", num: true, sortValue: (r) => num(r.unused_total), render: (r) => money(r.unused_total, ccy) },
    { key: "covered", header: "Covered (contract value)", num: true, info: "Pay-as-you-go value of benefit-covered usage.",
      sortValue: (r) => (r.eligible ? num(r.covered) : null), render: (r) => (r.eligible ? money(r.covered, ccy) : "—") },
    { key: "od", header: "On-demand (contract value)", num: true,
      info: "Usage in this category still billed on demand. It is spend to review, not a forecast saving.",
      sortValue: (r) => (r.eligible ? num(r.on_demand) : null), render: (r) => (r.eligible ? money(r.on_demand, ccy) : "—") },
    { key: "cov", header: "Coverage", num: true, sortValue: (r) => r.coverage, render: (r) => pct(r.coverage) },
    { key: "net", header: "Net discount", num: true, info: "Gross discount minus unused commitment cost.",
      sortValue: (r) => num(r.net), render: (r) => money(r.net, ccy) },
  ];
  const odCols: Col<CommitmentCategory>[] = [
    { key: "cat", header: "Service", sortValue: (r) => r.category ?? "", render: (r) => r.category ?? "(none)" },
    { key: "od", header: "On-demand (contract value)", num: true, sortValue: (r) => num(r.on_demand), render: (r) => money(r.on_demand, ccy) },
    { key: "cov", header: "Coverage", num: true, sortValue: (r) => r.coverage, render: (r) => pct(r.coverage) },
    { key: "unused", header: "Unused commitment", num: true, sortValue: (r) => num(r.unused_total), render: (r) => money(r.unused_total, ccy) },
  ];
  const monthCols: Col<CommitmentMonth>[] = [
    { key: "month", header: "Month", sortValue: (r) => r.month, render: (r) => monthLabel(r.month) },
    { key: "util", header: "Reservation utilization", num: true, sortValue: (r) => r.utilization, render: (r) => pct(r.utilization) },
    { key: "cov", header: "Coverage", num: true, sortValue: (r) => r.coverage, render: (r) => pct(r.coverage) },
    { key: "unused", header: "Unused cost", num: true, sortValue: (r) => num(r.unused_total), render: (r) => money(r.unused_total, ccy) },
    { key: "gross", header: "Gross discount", num: true, sortValue: (r) => num(r.gross), render: (r) => money(r.gross, ccy) },
    { key: "net", header: "Net discount", num: true, sortValue: (r) => num(r.net), render: (r) => money(r.net, ccy) },
  ];
  const resCols: Col<ReservationRow>[] = [
    { key: "name", header: "Reservation", sortValue: (r) => r.name ?? r.commitment_id,
      render: (r) => <span title={r.commitment_id}>{r.name ?? r.commitment_id}</span> },
    { key: "cat", header: "Meter category", sortValue: (r) => r.meter_category ?? "", render: (r) => r.meter_category ?? "—" },
    { key: "util", header: "Utilization", num: true, sortValue: (r) => r.utilization, render: (r) => <UtilBar value={r.utilization} /> },
    { key: "unused", header: "Unused cost", num: true, sortValue: (r) => num(r.unused_cost), render: (r) => money(r.unused_cost, ccy, 2) },
    { key: "used", header: "Used cost", num: true, sortValue: (r) => num(r.used_cost), render: (r) => money(r.used_cost, ccy, 2) },
  ];

  return (
    <div className={det.loading ? "loading" : ""} aria-busy={det.loading}>
      <p className="muted small">
        Observed from the amortized export, in {ccy}: what was used and what was not. Price-sheet savings are ceilings,
        not purchase advice. Hourly sizing also needs hourly eligible usage and existing commitment coverage.
      </p>
      <div className="kpis">
        <Kpi label="Reservation utilization" value={pct(u?.ratio_cost)} note="share of reservation spend that was used" info={u?.definition} />
        <Kpi label="Commitment coverage" value={pct(cov?.ratio)} note="contract value, inferred eligibility" info={cov?.definition} />
        <Kpi label="Net observed discount" value={moneyShort(disc?.net ?? 0, ccy)} full={money(disc?.net ?? 0, ccy)} note={`gross ${money(disc?.gross ?? 0, ccy)}`} info={disc?.definition} />
        <Kpi label="Unused commitment" value={moneyShort(unusedTotal, ccy)} full={money(unusedTotal, ccy)}
          note={spend ? `reservations ${money(spend.unused_reservation, ccy)} · plans ${money(spend.unused_savings_plan, ccy)}` : undefined}
          info="Amortized cost of reserved or committed capacity that nothing used." />
        <Kpi label="Reservation usage" value={moneyShort(spend?.used_cost ?? 0, ccy)} full={money(spend?.used_cost ?? 0, ccy)} note="amortized cost of covered usage" />
        <Kpi label="Savings-plan usage" value={moneyShort(spend?.savings_plan_cost ?? 0, ccy)} full={money(spend?.savings_plan_cost ?? 0, ccy)} note="amortized cost of covered usage" />
      </div>

      <SpHourly filters={filters} />

      <section className="card" aria-label="Reservations with the most unused cost">
        <header className="card-head"><h3>Reservations with the most unused cost</h3>
          <span className="muted">{res.data?.available ? `${count(res.data.count)} reservations in scope` : ""}</span></header>
        {res.error && <p className="error small" role="alert">{res.error}</p>}
        {res.data && res.data.available && (
          <>
            <SortTable caption="Reservations ranked by unused cost" columns={resCols} rows={res.data.rows}
              rowKey={(r) => r.commitment_id} initialSort={{ key: "unused", desc: true }} empty="No reservations in this selection." />
            <p className="muted small">
              Monthly grain: the date range widens to whole months here.
              {res.data.filters_not_applied.length > 0 && ` Not applied at this grain: ${res.data.filters_not_applied.join(", ")}.`}
              {res.data.without_used_rows > 0 && ` ${res.data.without_used_rows} reservations show unused hours but no used hours; their used and unused rows may carry different ids.`}
              {" "}Showing the top {res.data.rows.length}. New reservations fill up over time, so judge a reservation started mid-month over several months.
            </p>
          </>
        )}
        {!res.data && !res.error && <p className="empty">Loading…</p>}
      </section>

      <section className="card" aria-label="Commitments by meter category">
        <header className="card-head"><h3>By meter category</h3>
          <span className="muted">{count(d.category_count)} categories with commitment activity</span></header>
        <SortTable caption="Commitment metrics by meter category" columns={catCols} rows={d.by_category}
          rowKey={(r) => r.category ?? "(none)"} initialSort={{ key: "unused", desc: true }} empty="No commitment activity in this selection." />
        {d.category_count > d.by_category.length && <p className="muted small">Showing the first {d.by_category.length} of {d.category_count}.</p>}
      </section>

      <section className="card" aria-label="On-demand spend alongside commitments">
        <header className="card-head"><h3>On-demand spend in services that already have commitments</h3></header>
        <SortTable caption="On-demand spend in services with commitments" columns={odCols}
          rows={d.by_category.filter((x) => x.eligible)} rowKey={(r) => r.category ?? "(none)"}
          initialSort={{ key: "od", desc: true }} empty="No service has commitment activity in this selection." />
        <p className="muted small">
          Contract value of usage that was not covered, in services where the export shows reservation or savings-plan usage. It is spend to
          review, not a saving: coverage may be intentionally partial.
        </p>
      </section>

      <SavingsHeadroom filters={filters} />

      <section className="card" aria-label="Commitments by month">
        <header className="card-head"><h3>By month</h3></header>
        <SortTable caption="Commitment metrics by month" columns={monthCols} rows={d.by_month}
          rowKey={(r) => r.month} initialSort={{ key: "month", desc: false }} empty="No months in this selection." />
      </section>

      <details className="card">
        <summary>How these are calculated</summary>
        <ul className="defs">
          {u && <li><strong>Utilization.</strong> {u.definition}</li>}
          {cov && <li><strong>Coverage.</strong> {cov.definition} Covered and eligible usage are both valued at the contract unit price;
            {" "}{count(cov.contract_basis_rows)} benefit rows use it as their baseline, and {count(cov.excluded_missing_price_rows)} rows
            with no price recorded are left out, not counted as zero.</li>}
          {disc && <li><strong>Observed discount.</strong> {disc.definition}</li>}
        </ul>
      </details>
      <TimingNote timing={det.timing} loading={det.loading} />
    </div>
  );
}
