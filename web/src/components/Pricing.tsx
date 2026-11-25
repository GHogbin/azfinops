import { useMemo } from "react";
import { filterParams, type Filters, type PriceRow, type Pricing as Data } from "../api";
import { count, money, num, pct, moneyShort } from "../format";
import { useApi } from "../hooks";
import { ExchangeRates } from "./ExchangeRates";
import { Kpi } from "./Kpi";
import { SortTable, type Col } from "./SortTable";
import { ErrorBox, TimingNote } from "./Status";

interface Props { filters: Filters }

export function Pricing({ filters }: Props) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  const params = useMemo(() => { const p = filterParams(filters); p.set("n", "15"); return p; }, [filters]);
  const { data, error, loading, reload, timing } = useApi<Data>(ok ? "/api/pricing" : null, ok ? params : null);
  const ccy = filters.currency;
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <p className="empty" aria-live="polite">{ok ? "Loading…" : "Fix the date range to load data."}</p>;
  const t = data.totals;
  const ref = data.reference;

  const cols = (label: string): Col<PriceRow>[] => [
    { key: "key", header: label, sortValue: (r) => r.key ?? "", render: (r) => r.key ?? "(none)" },
    { key: "cost", header: "Charged", num: true, sortValue: (r) => num(r.cost_with_payg), info: "Cost of the rows that have a contract price.",
      render: (r) => money(r.cost_with_payg, ccy) },
    { key: "payg", header: "contract value", num: true, sortValue: (r) => num(r.payg_equivalent), info: "contract price × quantity over the same rows.",
      render: (r) => money(r.payg_equivalent, ccy) },
    { key: "ratio", header: "Paid vs contract", num: true, info: "Charged ÷ contract value. 100% means the contract price.",
      sortValue: (r) => r.ratio_paid, render: (r) => pct(r.ratio_paid) },
    { key: "known", header: "Priced share", num: true, info: "Share of the usage cost that has a contract price and so could be compared.",
      sortValue: (r) => r.known_share, render: (r) => pct(r.known_share) },
    { key: "missing", header: "No contract price", num: true, info: "Usage rows left out because the export has no contract price. Never valued at zero.",
      sortValue: (r) => r.missing_rows, render: (r) => count(r.missing_rows) },
  ];

  return (
    <div className={loading ? "loading" : ""} aria-busy={loading}>
      <p className="muted small">What was charged against the contract-price value of the same usage, in {ccy}. Observed from the export only.</p>
      <div className="kpis">
        <Kpi label="Paid vs contract" value={pct(t.ratio_paid)} note="usage rows with a contract price" info={data.definition} />
        <Kpi label="Charged" value={moneyShort(t.cost_with_payg, ccy)} full={money(t.cost_with_payg, ccy)} note="on those rows" />
        <Kpi label="contract value" value={moneyShort(t.payg_equivalent, ccy)} full={money(t.payg_equivalent, ccy)} note="same usage at contract price" />
        <Kpi label="Priced share" value={pct(t.known_share)} note="of usage cost" />
        <Kpi label="No contract price" value={count(t.missing_rows)} note="rows left out, not zero" />
        <Kpi label="At contract price" value={count(t.contract_rows)} note="valued at contract unit price" />
      </div>

      <section className="card" aria-label="By pricing model">
        <header className="card-head"><h3>By pricing model</h3></header>
        <SortTable caption="Paid against contract by pricing model" columns={cols("Pricing model")} rows={data.by_pricing_model}
          rowKey={(r) => r.key ?? "(none)"} initialSort={{ key: "payg", desc: true }} />
        <p className="muted small">Marketplace and spot usage have no meaningful contract value, so read their ratios with care.</p>
      </section>

      <section className="card" aria-label="By service">
        <header className="card-head"><h3>By service</h3><span className="muted">top {data.by_service.length} of {count(data.service_groups)} by contract value</span></header>
        <SortTable caption="Paid against contract by service" columns={cols("Service")} rows={data.by_service}
          rowKey={(r) => r.key ?? "(none)"} initialSort={{ key: "payg", desc: true }} />
      </section>

      <section className="card" aria-label="Reference data">
        <header className="card-head"><h3>Reference data loaded</h3></header>
        <ul className="defs">
          <li><strong>Price list.</strong> {ref.price_benchmarks === 0
            ? "None loaded. Without a price list nothing here is compared with a retail or negotiated price, and no saving is estimated."
            : `${count(ref.price_benchmarks)} price entries loaded.`}</li>
          <li><strong>Subscription names.</strong> {count(ref.subscriptions_named)} of {count(ref.subscriptions_in_data)} subscriptions in the data have a name.</li>
          <li><strong>Exchange rates.</strong> Looked up and recorded below. They are used only by the Combined option in Filters; otherwise each currency is shown in its own native value.</li>
        </ul>
      </section>
      <ExchangeRates />
      <TimingNote timing={timing} loading={loading} />
    </div>
  );
}
