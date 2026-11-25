-- 0008: five more export columns are kept on the facts (BillingAccountName, MeterRegion, MeterSubCategory,
-- EffectivePrice, ResourceName). The fact table gains nullable columns; existing rows read NULL until their month is
-- re-imported (use --supersede). The committed-only view is recreated so it picks up the new columns.
-- The aggregates gain meter_subcategory (agg_daily) and meter_subcategory, meter_region, resource_name
-- (agg_resource_month). They are derived data and are emptied here: a re-import rebuilds the months it touches, or run
-- `azfinops-aggregate`.

DROP VIEW v_cost_facts;

ALTER TABLE cost_facts ADD COLUMN billing_account_name VARCHAR;
ALTER TABLE cost_facts ADD COLUMN meter_region VARCHAR;
ALTER TABLE cost_facts ADD COLUMN meter_subcategory VARCHAR;
ALTER TABLE cost_facts ADD COLUMN effective_price DECIMAL(38,18);
ALTER TABLE cost_facts ADD COLUMN resource_name VARCHAR;

CREATE VIEW v_cost_facts AS
SELECT f.* FROM cost_facts f
JOIN import_batches b ON b.batch_id = f.batch_id
WHERE b.status = 'committed';

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
  meter_subcategory VARCHAR,
  charge_type VARCHAR NOT NULL,
  pricing_model VARCHAR,
  publisher_type VARCHAR,
  application VARCHAR NOT NULL,
  environment VARCHAR NOT NULL,
  cost DECIMAL(38,18) NOT NULL,
  payg_equivalent DECIMAL(38,18),       -- sum(payg_price * quantity) where payg_price is known
  cost_with_payg DECIMAL(38,18),        -- sum(cost) over the same rows
  payg_missing_rows BIGINT NOT NULL,    -- Usage rows without a usable baseline price
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
  resource_name VARCHAR,
  meter_category VARCHAR,
  meter_subcategory VARCHAR,
  meter_region VARCHAR,
  meter_id VARCHAR,
  meter_name VARCHAR,
  unit VARCHAR,
  charge_type VARCHAR NOT NULL,
  pricing_model VARCHAR,
  commitment_id VARCHAR,
  reservation_name VARCHAR,
  offer_id VARCHAR,
  application VARCHAR NOT NULL,
  environment VARCHAR NOT NULL,
  cost DECIMAL(38,18) NOT NULL,
  quantity DECIMAL(38,18),
  row_count BIGINT NOT NULL,
  is_synthetic BOOLEAN NOT NULL
);
