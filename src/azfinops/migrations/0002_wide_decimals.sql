-- 0002: real exports carry up to ~22 decimal places in Cost and floating-point noise in Quantity.
-- Widen to DECIMAL(38,18); drop the primary key on cost_facts (bulk-load cost; row identity
-- (batch_id, file_id, row_number) is unique by construction); add columns seen in real exports;
-- add the committed-only view every query must use.
-- NOTE: recreates the fact and aggregate tables. Only valid before any data is imported.
-- requires-empty: cost_facts

DROP TABLE cost_facts;
DROP TABLE agg_daily;
DROP TABLE agg_resource_month;

CREATE TABLE cost_facts (
  batch_id BIGINT NOT NULL,
  file_id BIGINT NOT NULL,
  row_number BIGINT NOT NULL,
  cost_kind VARCHAR NOT NULL CHECK (cost_kind IN ('actual','amortized')),
  usage_date DATE NOT NULL,
  usage_month DATE NOT NULL,
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
  quantity DECIMAL(38,18),
  unit_price DECIMAL(38,18),
  payg_price DECIMAL(38,18),     -- NULL = unknown; see payg_basis
  payg_basis VARCHAR,            -- 'payg' (export PayGPrice) | 'contract' (UnitPrice fallback on benefit rows) | NULL
  cost DECIMAL(38,18) NOT NULL,
  charge_type VARCHAR NOT NULL,
  pricing_model VARCHAR,
  publisher_type VARCHAR,
  publisher_name VARCHAR,
  frequency VARCHAR,
  reservation_id VARCHAR,
  reservation_name VARCHAR,
  benefit_id VARCHAR,
  tags VARCHAR,
  consumed_service VARCHAR,
  offer_id VARCHAR,
  term VARCHAR,
  is_synthetic BOOLEAN NOT NULL
);

CREATE TABLE agg_daily (
  usage_date DATE NOT NULL,
  usage_month DATE NOT NULL,
  cost_kind VARCHAR NOT NULL,
  billing_account_id VARCHAR NOT NULL,
  billing_currency VARCHAR NOT NULL,
  subscription_id VARCHAR,
  resource_group VARCHAR,
  region VARCHAR,
  meter_category VARCHAR,
  charge_type VARCHAR NOT NULL,
  pricing_model VARCHAR,
  publisher_type VARCHAR,
  cost DECIMAL(38,18) NOT NULL,
  payg_equivalent DECIMAL(38,18),
  payg_missing_rows BIGINT NOT NULL,
  contract_basis_rows BIGINT NOT NULL,
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
  cost DECIMAL(38,18) NOT NULL,
  quantity DECIMAL(38,18),
  payg_equivalent DECIMAL(38,18),
  payg_missing_rows BIGINT NOT NULL,
  row_count BIGINT NOT NULL,
  is_synthetic BOOLEAN NOT NULL
);

-- Facts of failed, running, rolled-back or superseded batches are invisible through this view.
CREATE VIEW v_cost_facts AS
SELECT f.* FROM cost_facts f
JOIN import_batches b ON b.batch_id = f.batch_id
WHERE b.status = 'committed';
