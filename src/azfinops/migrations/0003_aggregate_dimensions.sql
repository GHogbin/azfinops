-- 0003: aggregates gain application/environment (from tags), hour quantities for utilization,
-- and cost_with_payg so observed-discount arithmetic uses the same rows on both sides.
-- Aggregates are derived data: dropping them is safe (rebuild with `azfinops-aggregate`).

DROP TABLE agg_daily;
DROP TABLE agg_resource_month;
DELETE FROM aggregate_freshness;

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
  application VARCHAR NOT NULL,
  environment VARCHAR NOT NULL,
  cost DECIMAL(38,18) NOT NULL,
  payg_equivalent DECIMAL(38,18),       -- sum(payg_price * quantity) where payg_price is known
  cost_with_payg DECIMAL(38,18),        -- sum(cost) over the same rows
  payg_missing_rows BIGINT NOT NULL,    -- Usage rows without a usable payg_price
  contract_basis_rows BIGINT NOT NULL,  -- rows valued at UnitPrice (payg_basis = 'contract')
  quantity_hours DECIMAL(38,18),        -- sum(quantity) where unit = '1 Hour'
  row_count BIGINT NOT NULL,
  is_synthetic BOOLEAN NOT NULL
);

CREATE TABLE agg_resource_month (
  usage_month DATE NOT NULL,
  cost_kind VARCHAR NOT NULL,
  billing_currency VARCHAR NOT NULL,
  subscription_id VARCHAR,
  resource_group VARCHAR,
  resource_id VARCHAR,
  meter_category VARCHAR,
  meter_id VARCHAR,
  meter_name VARCHAR,
  unit VARCHAR,
  charge_type VARCHAR NOT NULL,
  pricing_model VARCHAR,
  commitment_id VARCHAR,
  application VARCHAR NOT NULL,
  environment VARCHAR NOT NULL,
  cost DECIMAL(38,18) NOT NULL,
  quantity DECIMAL(38,18),
  row_count BIGINT NOT NULL,
  is_synthetic BOOLEAN NOT NULL
);
