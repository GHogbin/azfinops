# Canonical data model and ingestion contract

Schema: the forward-only, checksummed migrations in `src/azfinops/migrations/`, through
`0010_aggregate_integrity.sql`. Existing databases are backed up before applying pending migrations.
Migration 0010 is required by the updated server and must be applied with the server stopped.

Tables: `import_batches`, `import_files` (sha256 lineage), `validation_results`, `billing_accounts`,
`subscriptions`, `resources`, `cost_facts` (identity = batch, file, row — identical billing rows are
preserved), `commitments`, `exchange_rates` (provenance required), `price_benchmarks`,
`utilization_observations`, and aggregates `agg_daily`, `agg_resource_month`, `aggregate_freshness`
(rebuilt per `usage_month`). Price sheets use `price_sheet` and `price_sheet_files`; subscription
labels use `dim_subscription`. Foreign keys are logical, enforced by the importer.

Integrity metadata:

- `aggregate_scope_freshness`: one row per month, basis, enrollment and native currency, with first/last
  date, distinct dates present, source row count, completeness and rebuild timestamp. Migration 0010
  populates it from existing daily aggregates; subsequent rebuilds replace the affected months.
- `aggregate_invalidations`: pending month/basis rebuilds, recorded atomically with an ingestion commit.
  A successful month rebuild updates both aggregate tables and scoped metadata before clearing its
  invalidations. Pending entries survive restarts and block cost endpoints, not recovery diagnostics.

The migration also marks committed imports newer than their recorded aggregate build, or lacking a
known build, for recovery. A full rebuild clears pending entries for empty or removed months too.

## Canonical CSV (our import format)
Columns: see `src/azfinops/canonical.py`. Required: `date, billing_account_id, billing_currency,
cost, charge_type`. Subscription is optional; absent optional values become NULL.
`x_synthetic=true` marks synthetic rows. The cost basis (`actual`/`amortized`) is declared per file at
import, not per row.

## Duplicate and re-import handling
- Identical rows inside one file are legitimate billing rows and are kept.
- A file whose sha256 already exists in a committed batch is rejected as a re-import.
- A batch overlapping an existing batch's (account, cost kind, period) must be explicitly
  superseded (old batch marked `superseded`, aggregates rebuilt) or is rejected.
- A replacement must include every enrollment/currency/date tuple in every batch it supersedes.
  Replacing September alone cannot supersede a batch that also contains August; omitting an enrollment,
  currency or interior date is also refused. Old committed data remains visible if the check fails.
- `--reimport` permits an identical file only together with `--supersede`.
- Supply all CSV partitions of one export as one batch. The current importer does not validate Azure
  manifests, so date completeness alone cannot prove that every partition was supplied.
- Fact commits and aggregate rebuilds are separate stages. A failed rebuild does not roll back an
  already committed import; durable invalidations prevent stale dashboard totals from being served.

## Rolling retention

Successful cost imports enforce one portfolio-wide 12-calendar-month window, anchored to the newest
committed cost month (`import_batches.period_end`), not today's date and not the last twelve observed
non-empty months. The newest month and its eleven preceding months are retained across all bases,
enrollments and currencies. For October 2026 the inclusive cutoff is `2025-11-01`.

The import transaction deletes older rows from `cost_facts` regardless of batch status and from
`agg_daily`, `agg_resource_month`, `aggregate_freshness`, `aggregate_scope_freshness` and
`aggregate_invalidations`. Only retained changed months need rebuilding. A retention failure rolls
back the import commit and purge together; a later rebuild failure still uses durable recovery guards.

Import/file hashes, original periods, accepted/rejected counts and validation history remain. Each
affected batch gets an informational `retention_purged_rows` validation entry: `sample_value` holds
the cumulative integer count of physically removed fact rows, and its message names the cutoff.
These original source counts must not be presented as the count currently stored.

An entirely expired export is refused; a mixed-period export reports accepted and retained rows
separately. Future cost months are refused before they can advance retention. Source CSVs, price
sheets, rates, settings and existing backups are unchanged. Purging does not require a schema
migration or guarantee physical file shrinkage.

## Azure export compatibility — what was verified (Microsoft Learn, retrieved 2026-10-30)
EA amortized CSV imports have been exercised on real data; production datasets and deployment observations
are not included in this repository. Automated fixtures are synthetic. MCA aliases are based on
documentation and are not real-export verified.
- EA cost and usage details, schema 2024-08-01, 57 columns (PascalCase, e.g. `CostInBillingCurrency`,
  `PricingModel`, `benefitId`).
- MCA cost and usage details, schema 2023-12-01-preview, 65 columns (camelCase, e.g.
  `costInBillingCurrency`, `costInUsd`, `exchangeRatePricingToBilling`).
- Dataset types: actual, amortized, FOCUS, usage-only, price sheet, reservation details/
  recommendations/transactions. CSV (none/gzip) and Parquet (none/snappy); partitioned; `manifest.json`.
- `ChargeType`: Usage, Purchase, Refund. `UnusedReservation` appears in amortized data.
- The docs do not state date formats or column data types: ISO `yyyy-mm-dd` and `M/D/YYYY` will be
  accepted but are **unverified**. `UnusedSavingsPlan` was observed in an EA amortized export but is not in the
  Microsoft documentation I read. `Credit` and `Adjustment` remain our extensions, unconfirmed.
- Real-export findings (EA amortized, 55 columns): dates `MM/DD/YYYY`; `PricingModel` is `OnDemand`;
  `Tags` lacks outer braces; `PayGPrice` is 0 on reservation rows; `ServiceFamily` is `Compute` on
  every row (unusable, use `MeterCategory`); costs carry up to 22 decimals. ResourceName,
  MeterSubCategory, MeterRegion, EffectivePrice and BillingAccountName are now mapped; InvoiceSection
  and CostCenter are not analysis dimensions.

Unsupported: FOCUS (explicitly rejected), management-group-scope exports, Parquet and reservation
recommendation imports. EA price-sheet CSVs have a separate implemented importer. Saved FX rates,
budgets, views and enrollment names use side files rather than database writes.
