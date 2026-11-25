-- 0009: agg_resource_month gains the enrollment and when each resource ran in the month (first and last usage date,
-- days with usage), so the Optimization tab can find resized and decommissioned resources and compare their cost per
-- day. Existing rows keep NULL in the new columns until `azfinops-aggregate` rebuilds them; the screen says so.

ALTER TABLE agg_resource_month ADD COLUMN billing_account_id VARCHAR;
ALTER TABLE agg_resource_month ADD COLUMN first_date DATE;
ALTER TABLE agg_resource_month ADD COLUMN last_date DATE;
ALTER TABLE agg_resource_month ADD COLUMN active_days INTEGER;
