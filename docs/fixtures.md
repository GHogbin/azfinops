# Reconciled fixture: independently specified expectations

Files: `tests/fixtures/reconciled/{actual,amortized}.csv`, `expected.json`. All rows are synthetic.
Expected numbers were derived by hand from the scenario below, not produced by the code under test.

Scenario (USD account EA-T1 unless noted)
- Meter M1 (on-demand VM hours): Jan 10 h @ 1.00 = 10.00; Feb 20 h @ 1.15 = 23.00.
- Meter M2 (on-demand VM): Feb 1 h @ 1.00 = 1.00.
- Marketplace subscription: Feb 20.00.
- Reservation R1, PayG 4.00/h, reservation rate 1.00/h, 8 reserved hours available on 2026-02-10:
  3 hours used, 5 unused. Purchase 1,200.00 on 2026-02-01 (actual export only).
- Marketplace refund -50.00 and a credit -5.00 (actual export only; see methodology).
- GBP account MCA-T2: 100.00 GBP of on-demand usage (kept out of every USD total).

Derivations
- Actual USD total = usage (10 + 23 + 1 + 20 + 0 reserved) 54 + purchase 1200 + refund -50 + credit -5 = **1199.00**.
- Amortized USD total = 10 + 23 + 1 + 20 + reserved used 3 + unused 5 = **62.00**.
  (This tool's amortized fixtures contain usage and unused rows only; whether real amortized
  exports include refunds is unverified.)
- Utilization = 3 / (3 + 5) = **37.5%** (hours).
- Coverage, Feb: covered PayG-equivalent 3 h x 4.00 = 12; uncovered eligible on-demand
  (20 x 1.15) + (1 x 1.00) = 24; 12 / 36 = **33.33%**. Marketplace is not benefit-eligible.
- Observed discount: gross = 12 - 3 = **9**; net = 9 - unused cost 5 = **4**.
- Price/volume, M1 Jan->Feb: change 23 - 10 = 13; volume = (20 - 10) x 1.00 = **10**;
  price = (1.15 - 1.00) x 20 = **3**.
- FX: 100 GBP x 1.25 (synthetic rate) = **125.00 USD**, as a labelled presentation view only.
- Double counting: summing the actual and amortized files together must raise `MixedBasisError`.

## Importer fixture (`tests/fixtures/ea_amortized/`)
Layout mirrors a real EA amortized export; **all values are invented**. Rebuild with
`python tests/fixtures/build_ea_fixture.py`. `ea_amortized_sep.csv` has 11 data rows: 9 valid, 2
deliberately invalid (bad date row 10, bad cost row 11).
- Loaded total = 5 + 5 (two identical rows, both kept) + 0 + 1.6 + 0.35 + 0.8 + 0 + 1 + 2.5 = **16.25 GBP**.
- Reservation hours: used 4; unused 2 + 0 -> utilization 4 / 6 = **66.7%**.
- Reservation row PayGPrice 0 -> stored as UnitPrice 0.8 with basis `contract`; unused rows have no price (NULL).
- Brace-less tags `"app": "x","env": "dev"` -> valid JSON object.
