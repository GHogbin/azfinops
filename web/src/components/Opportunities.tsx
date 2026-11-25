import type { Commitments, Filters } from "../api";
import { moneyShort, num } from "../format";

/** Where cost could come down, from figures the Overview already has. Links to the Commitments workspace. */
export function Opportunities({ c, filters, onOpen }: { c: Commitments; filters: Filters; onOpen: () => void }) {
  const ccy = filters.currency;
  const unused = c.available && c.observed_discount ? num(c.observed_discount.unused_cost) : null;
  // Eligible usage is covered usage plus on-demand usage in services that have commitments, both at contract price.
  const onDemand = c.available && c.coverage ? Math.max(0, num(c.coverage.eligible) - num(c.coverage.covered)) : null;
  return (
    <section className="card tile-opps" aria-label="Opportunities">
      <header className="card-head"><h3>Opportunities</h3>
        <button type="button" className="link-btn" onClick={onOpen}>Open Commitments →</button></header>
      {!c.available ? <p className="muted small">{c.reason ?? "These figures need the amortized basis."}</p> : (
        <ul className="opps">
          <li>
            <span className="opp-value">{unused === null ? "n/a" : moneyShort(unused, ccy)}</span>
            <span>Unused commitment</span>
            <span className="muted small">Reservations and savings plans paid for but not used.</span>
          </li>
          <li>
            <span className="opp-value">{onDemand === null ? "n/a" : moneyShort(onDemand, ccy)}</span>
            <span>On-demand in services that already have commitments</span>
            <span className="muted small">At contract price. Spend to review, not a saving: coverage may be partial on purpose.</span>
          </li>
          <li>
            <span className="opp-value">See tab</span>
            <span>Savings-plan headroom</span>
            <span className="muted small">{filters.combined
              ? "An upper bound per enrollment, each in its own currency, on the Commitments tab."
              : "An upper bound from the price sheet, on the Commitments tab."}</span>
          </li>
        </ul>
      )}
    </section>
  );
}
