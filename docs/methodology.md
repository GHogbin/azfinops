# Financial methodology

Status: the calculations below are implemented in `src/azfinops/finance/calc.py` and tested against
`docs/fixtures.md`. Everything in demonstration mode is **synthetic**; no price, discount, saving or
licensing assumption in the demo data is an Azure fact.

## 1. Cost bases (never mixed)
| Basis | Source export | Contains | Reconciles to |
|---|---|---|---|
| **Actual / billed** | Cost and usage details (actual) | Usage, Purchase, Refund (+ our `Credit`/`Adjustment` extensions) | Invoice |
| **Effective / amortized** | Cost and usage details (amortized) | Usage at amortized benefit rates, `UnusedReservation` (and our `UnusedSavingsPlan` extension) | Not the invoice |

Every fact row carries `cost_kind`. Queries select exactly one basis; the code raises `MixedBasisError`
when rows of both are combined. Per Microsoft's reservation documentation, actual data shows
reservation-discounted usage at zero effective price plus a separate Purchase row, whereas amortized
data spreads the purchase across consuming usage and adds unused rows. Summing both would count the
same money twice.

Amortized exports can contain zero-cost `Purchase` rows as well as `UnusedReservation` and
`UnusedSavingsPlan` rows. The importer flags non-zero Purchase/Refund rows rather than assuming
that every Purchase row indicates a mixed cost basis.

Fixture simplification: the original small amortized fixtures contain
usage and unused rows only; refunds, credits and adjustments appear only in actual data.

## 2. Cost field precedence
1. `cost` (mapped from `CostInBillingCurrency` / `costInBillingCurrency`) is authoritative, in billing currency.
2. `quantity × unit_price` or `× effective price` is **never** assumed to equal `cost`; it is used only
   for price/volume analysis and only as `cost / quantity`.
3. `costInUsd` / pricing-currency fields (MCA) are stored when present but are not used for totals.
4. A missing `cost` rejects the row. A missing `payg_price` is stored as NULL and excluded (and counted)
   wherever it is needed — never treated as zero.

Precision: billed/amortized `cost` and `quantity` are summed as exact decimals (38,18). The PayG-equivalent
(`payg_price x quantity`) is computed in double precision (relative error far below 1e-9) because the exact
decimal product overflows on real data; it is a list-price estimate, never a billed amount.

Units: quantities are compared only within the same `(meter_id, unit)`. Native cost is stored as
DECIMAL(38,18); Python accumulation preserves that precision. The API serialises monetary values as
decimal strings rounded half-even to four places; the UI formats those for display.

## 3. Charge classification
`Usage` (incl. `pricing_model` On Demand / Spot / Reservation / SavingsPlan), `Purchase`, `Refund`,
`UnusedReservation`, `UnusedSavingsPlan`, and the extensions `Credit`, `Adjustment`. Marketplace is
`publisher_type = Marketplace`. Signed values are preserved: refunds and credits are negative and are
not netted away into usage. "Usage cost" excludes Purchase/Refund/Credit/Adjustment.

## 4. Metric definitions
- **Reservation utilization** = used hours / (used + unused hours), amortized basis. `None` (shown as
  "not available") if no capacity rows exist — never 0%.
- **Coverage** = covered PayG-equivalent / (covered + uncovered eligible on-demand PayG-equivalent),
  where the baseline is the contract UnitPrice by default (`PayGPrice` requires the import opt-in).
  The dashboard infers eligible categories from observed covered usage; it is not a published eligibility list.
  Spot, Marketplace and missing baseline prices are excluded, with missing prices counted.
- **Observed benefit discount** (historical, not an estimate): gross = PayG-equivalent of covered usage −
  its amortized cost; net = gross − cost of unused reservation/savings-plan rows.
- **Price vs volume** for a (meter, unit) present with non-zero quantity in both periods:
  volume = (q1 − q0)·p0, price = (p1 − p0)·q1 = c1 − q1·p0, p = cost / quantity;
  volume + price = c1 − c0 exactly. Groups new in period 1, removed, or with zero quantity are reported
  separately (`new`, `removed`, `unattributed`) and are never split.
- **Estimated optimization opportunity**, **forecast** and **hypothetical scenario** are separate objects
  with explicit assumptions and are never added to observed values. A Reservation and a Savings Plan
  opportunity for the same usage are mutually exclusive (no overlapping totals).
- Billing-only data cannot show idleness or rightsizing suitability; those signals require
  utilization observations and are otherwise shown as "insufficient evidence".
- **Savings-plan headroom** is a monthly ceiling, matched by month, meter, offer and normalised billing
  unit. Matched, unmatched, ambiguous and no-price-sheet cost reconcile to all selected on-demand usage.
  Cost without a meter ID remains in unmatched cost, or no-price-sheet cost when that month has no sheet.
- **Hourly purchase recommendations are unavailable.** A daily category total can stay steady while
  eligible meters disappear. Even its lowest daily average is not an hourly demand floor. A real sizing
  exercise needs hourly demand for the same matched, eligible meters and existing commitment coverage.
  The one- and three-year ceilings are alternatives, not additive opportunities.

## 5. Currency
Native currencies are separate unless the Combined view is chosen. Saved rates and provenance live in
`fx_rates.json`, by month and currency pair. A month without its own rate uses the nearest saved month
for that currency, the earlier one on a tie, and is labelled estimated. A currency with no saved rate
is excluded and named; no rate is guessed.

Rates must be positive, below 1,000,000, and have at most 18 decimal places. Reverse rates are inverted
with decimal arithmetic and rounded half-even to 18 places. Foreign aggregate amounts are multiplied
using Arrow decimal256 intermediates, then rounded half-even once to DECIMAL(38,18). No monetary value
passes through binary floating point during conversion. Native amounts are not rescaled. The
conversion-details total uses the same row-level conversion as dashboard queries.

## 6. Completeness
Date completeness is recorded for each `(month, cost_kind, billing_account_id, billing_currency)` in
`aggregate_scope_freshness`. Every calendar date must be present in that scope. A selected month is
complete only when every selected enrollment/currency pair is complete, including a pair missing from
that month altogether. Service, tag and other analytical filters do not certify import completeness.

Latest-month presets and month comparisons use this scoped metadata. Incomplete or unknown periods
do not produce a month-on-month change, a price/volume comparison, or a resource-change run-rate.
The Overview checks the previous month as well as the current month.

This is date coverage, not manifest validation: an omitted CSV partition can still contain the same
dates as the supplied partitions. Always supply every partition of an export.

An ingestion commit also records `aggregate_invalidations` in the same transaction. Both aggregate
tables and their completeness metadata must be rebuilt before the corresponding invalidation is
cleared. While any invalidation remains, cost endpoints return 503 with recovery instructions,
including after a server restart. Import history and diagnostics remain available.

### Retained analysis window

After each successful cost import, analysis contains at most the newest imported cost month and its
eleven preceding calendar months. This single cutoff applies to the whole portfolio, across currencies
and actual/amortized bases, without mixing their money. The newest month may be partial; the retention
window does not certify completeness. The oldest retained month may lack its preceding comparison month.

Expiry removes stored facts and their whole-month derived data in the same ingestion-commit transaction.
Failed/refused ingestion does not advance the cutoff. Original import counts remain audit history, not
retained billing-record totals. Source CSVs, reference price sheets, FX rates and backups are preserved.
Future cost months and entirely expired exports are refused before destructive retention can advance.

## 7. Tags and attribution
Application attribution uses the first non-blank configured tag key (by default `mnd-applicationid`,
then `ApplicationID`) on each source row, folded to lower case during aggregation. Missing tags are
`(untagged)`; tags without a configured application value are `(unattributed)`. Conflicting keys use
the configured priority, not an inferred resource-group or subscription rule.

## 8. Known limits
Rates, discounts and eligibility lists are assumptions in demo mode. Savings estimates are estimates,
not guarantees. Forecast methods and uncertainty are documented where implemented (Phase 4).
