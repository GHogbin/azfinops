-- 0004: agg_resource_month gains reservation_name so utilization can be shown per reservation.
-- Aggregates are derived data: dropping them is safe (rebuild with `azfinops-aggregate`).

DROP TABLE agg_resource_month;
DELETE FROM aggregate_freshness WHERE aggregate_name = 'agg_resource_month';

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
  reservation_name VARCHAR,
  application VARCHAR NOT NULL,
  environment VARCHAR NOT NULL,
  cost DECIMAL(38,18) NOT NULL,
  quantity DECIMAL(38,18),
  row_count BIGINT NOT NULL,
  is_synthetic BOOLEAN NOT NULL
);
