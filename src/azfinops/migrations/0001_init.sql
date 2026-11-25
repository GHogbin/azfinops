-- 0001_init: canonical model. Logical foreign keys are documented in docs/data-model.md and
-- enforced by the importer (DuckDB FK constraints are deliberately not used).

CREATE TABLE import_batches (
  batch_id BIGINT PRIMARY KEY,
  source_kind VARCHAR NOT NULL,
  cost_kind VARCHAR NOT NULL CHECK (cost_kind IN ('actual','amortized','none')),
  is_synthetic BOOLEAN NOT NULL,
  status VARCHAR NOT NULL CHECK (status IN ('running','committed','failed','superseded','rolled_back')),
  period_start DATE,
  period_end DATE,
  row_count BIGINT,
  rejected_row_count BIGINT,
  supersedes_batch_id BIGINT,
  created_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP,
  notes VARCHAR
);

CREATE TABLE import_files (
  file_id BIGINT PRIMARY KEY,
  batch_id BIGINT NOT NULL,
  file_name VARCHAR NOT NULL,
  sha256 VARCHAR NOT NULL,
  byte_count BIGINT,
  row_count BIGINT,
  schema_id VARCHAR,
  partition_ordinal INTEGER
);

CREATE TABLE validation_results (
  batch_id BIGINT NOT NULL,
  file_id BIGINT,
  severity VARCHAR NOT NULL CHECK (severity IN ('error','warning','info')),
  code VARCHAR NOT NULL,
  row_number BIGINT,
  column_name VARCHAR,
  message VARCHAR NOT NULL,
  sample_value VARCHAR
);

CREATE TABLE billing_accounts (
  billing_account_id VARCHAR PRIMARY KEY,
  name VARCHAR,
  billing_currency VARCHAR NOT NULL,
  agreement_type VARCHAR,
  is_synthetic BOOLEAN NOT NULL
);

CREATE TABLE subscriptions (
  subscription_id VARCHAR PRIMARY KEY,
  name VARCHAR,
  billing_account_id VARCHAR,
  is_synthetic BOOLEAN NOT NULL
);

CREATE TABLE resources (
  resource_id VARCHAR PRIMARY KEY,
  name VARCHAR,
  subscription_id VARCHAR,
  resource_group VARCHAR,
  region VARCHAR,
  service_family VARCHAR,
  first_seen DATE,
  last_seen DATE,
  latest_tags VARCHAR,          -- JSON of the most recent tags observed (approximation, see methodology)
  is_synthetic BOOLEAN NOT NULL
);

CREATE TABLE cost_facts (
  batch_id BIGINT NOT NULL,
  file_id BIGINT NOT NULL,
  row_number BIGINT NOT NULL,   -- (batch, file, row) identity: identical billing rows stay distinct
  cost_kind VARCHAR NOT NULL CHECK (cost_kind IN ('actual','amortized')),
  usage_date DATE NOT NULL,
  usage_month DATE NOT NULL,    -- first day of month; partition key for aggregate rebuilds
  billing_account_id VARCHAR NOT NULL,
  billing_currency VARCHAR NOT NULL,
  subscription_id VARCHAR,
  resource_group VARCHAR,
  resource_id VARCHAR,
  region VARCHAR,
  service_family VARCHAR,
  meter_category VARCHAR,
  meter_id VARCHAR,
  meter_name VARCHAR,
  sku VARCHAR,
  unit VARCHAR,
  quantity DECIMAL(28,10),
  unit_price DECIMAL(28,10),
  payg_price DECIMAL(28,10),    -- NULL = unknown; never coerced to 0
  cost DECIMAL(28,10) NOT NULL, -- authoritative signed cost in billing currency
  charge_type VARCHAR NOT NULL,
  pricing_model VARCHAR,
  publisher_type VARCHAR,
  publisher_name VARCHAR,
  frequency VARCHAR,
  reservation_id VARCHAR,
  benefit_id VARCHAR,
  tags VARCHAR,
  is_synthetic BOOLEAN NOT NULL,
  PRIMARY KEY (batch_id, file_id, row_number)
);

CREATE TABLE commitments (
  commitment_id VARCHAR PRIMARY KEY,
  kind VARCHAR NOT NULL CHECK (kind IN ('Reservation','SavingsPlan')),
  name VARCHAR,
  billing_account_id VARCHAR,
  billing_currency VARCHAR NOT NULL,
  term_months INTEGER,
  start_date DATE,
  end_date DATE,
  purchase_cost DECIMAL(28,10),
  hourly_commitment DECIMAL(28,10),
  scope VARCHAR,
  sku VARCHAR,
  region VARCHAR,
  quantity DECIMAL(28,10),
  is_synthetic BOOLEAN NOT NULL
);

CREATE TABLE exchange_rates (
  rate_date DATE NOT NULL,
  from_currency VARCHAR NOT NULL,
  to_currency VARCHAR NOT NULL,
  rate DECIMAL(28,12) NOT NULL,
  source VARCHAR NOT NULL,       -- provenance is mandatory
  is_synthetic BOOLEAN NOT NULL,
  PRIMARY KEY (rate_date, from_currency, to_currency, source)
);

CREATE TABLE price_benchmarks (
  benchmark_id BIGINT PRIMARY KEY,
  source_name VARCHAR NOT NULL,
  source_ref VARCHAR,
  imported_at TIMESTAMP NOT NULL,
  effective_start DATE,
  effective_end DATE,
  meter_id VARCHAR,
  sku VARCHAR,
  region VARCHAR,
  unit VARCHAR NOT NULL,
  currency VARCHAR NOT NULL,
  term VARCHAR,
  price_type VARCHAR NOT NULL,   -- retail | contract | reservation | savings_plan | other
  unit_price DECIMAL(28,10) NOT NULL,
  is_synthetic BOOLEAN NOT NULL
);

CREATE TABLE utilization_observations (
  resource_id VARCHAR NOT NULL,
  observed_date DATE NOT NULL,
  metric VARCHAR NOT NULL,       -- e.g. cpu_percent
  aggregation VARCHAR NOT NULL,  -- avg | max | p95
  value DOUBLE NOT NULL,
  unit VARCHAR,
  source VARCHAR NOT NULL,
  batch_id BIGINT,
  is_synthetic BOOLEAN NOT NULL
);

-- Aggregates (rebuilt per usage_month from cost_facts of committed, non-superseded batches).
CREATE TABLE agg_daily (
  usage_date DATE NOT NULL,
  usage_month DATE NOT NULL,
  cost_kind VARCHAR NOT NULL,
  billing_account_id VARCHAR NOT NULL,
  billing_currency VARCHAR NOT NULL,
  subscription_id VARCHAR,
  resource_group VARCHAR,
  region VARCHAR,
  service_family VARCHAR,
  meter_category VARCHAR,
  charge_type VARCHAR NOT NULL,
  pricing_model VARCHAR,
  publisher_type VARCHAR,
  cost DECIMAL(28,10) NOT NULL,
  payg_equivalent DECIMAL(28,10),    -- sum(payg_price*quantity) over rows with a known payg_price
  payg_missing_rows BIGINT NOT NULL, -- rows excluded from payg_equivalent
  row_count BIGINT NOT NULL,
  is_synthetic BOOLEAN NOT NULL
);

CREATE TABLE agg_resource_month (
  usage_month DATE NOT NULL,
  cost_kind VARCHAR NOT NULL,
  billing_currency VARCHAR NOT NULL,
  subscription_id VARCHAR,
  resource_id VARCHAR,
  meter_id VARCHAR,
  unit VARCHAR,
  charge_type VARCHAR NOT NULL,
  pricing_model VARCHAR,
  commitment_id VARCHAR,
  cost DECIMAL(28,10) NOT NULL,
  quantity DECIMAL(28,10),
  payg_equivalent DECIMAL(28,10),
  payg_missing_rows BIGINT NOT NULL,
  row_count BIGINT NOT NULL,
  is_synthetic BOOLEAN NOT NULL
);

CREATE TABLE aggregate_freshness (
  aggregate_name VARCHAR NOT NULL,
  usage_month DATE NOT NULL,
  cost_kind VARCHAR NOT NULL,
  built_at TIMESTAMP NOT NULL,
  source_row_count BIGINT NOT NULL,
  source_batch_ids VARCHAR NOT NULL,
  period_complete BOOLEAN NOT NULL,  -- false when imported data does not cover the whole month
  PRIMARY KEY (aggregate_name, usage_month, cost_kind)
);
