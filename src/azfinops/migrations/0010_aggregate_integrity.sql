-- 0010: scoped completeness and durable invalidation of derived data.
CREATE TABLE aggregate_scope_freshness (
  usage_month DATE NOT NULL,
  cost_kind VARCHAR NOT NULL,
  billing_account_id VARCHAR NOT NULL,
  billing_currency VARCHAR NOT NULL,
  first_date DATE NOT NULL,
  last_date DATE NOT NULL,
  days_present INTEGER NOT NULL,
  source_row_count BIGINT NOT NULL,
  period_complete BOOLEAN NOT NULL,
  built_at TIMESTAMP NOT NULL,
  PRIMARY KEY (usage_month, cost_kind, billing_account_id, billing_currency)
);

INSERT INTO aggregate_scope_freshness
SELECT d.usage_month, d.cost_kind, d.billing_account_id, d.billing_currency,
       min(d.usage_date), max(d.usage_date), CAST(count(DISTINCT d.usage_date) AS INTEGER),
       sum(d.row_count),
       count(DISTINCT d.usage_date) = date_diff(
         'day', d.usage_month, CAST(d.usage_month + INTERVAL 1 MONTH AS DATE)),
       coalesce(max(f.built_at), CAST(now() AS TIMESTAMP))
FROM agg_daily d LEFT JOIN aggregate_freshness f
  ON f.aggregate_name = 'agg_daily' AND f.usage_month = d.usage_month AND f.cost_kind = d.cost_kind
GROUP BY d.usage_month, d.cost_kind, d.billing_account_id, d.billing_currency;

CREATE TABLE aggregate_invalidations (
  usage_month DATE NOT NULL,
  cost_kind VARCHAR NOT NULL,
  invalidated_at TIMESTAMP NOT NULL,
  batch_id BIGINT NOT NULL,
  PRIMARY KEY (usage_month, cost_kind)
);

INSERT INTO aggregate_invalidations
SELECT m.usage_month, b.cost_kind, max(coalesce(b.finished_at, b.created_at)), max(b.batch_id)
FROM import_batches b CROSS JOIN
     LATERAL (SELECT CAST(unnest(generate_series(
       CAST(date_trunc('month', b.period_start) AS DATE),
       CAST(date_trunc('month', b.period_end) AS DATE), INTERVAL 1 MONTH)) AS DATE) AS usage_month) m
LEFT JOIN aggregate_freshness f
  ON f.aggregate_name = 'agg_daily' AND f.usage_month = m.usage_month AND f.cost_kind = b.cost_kind
WHERE b.status = 'committed' AND (f.built_at IS NULL OR b.finished_at IS NULL OR b.finished_at > f.built_at)
GROUP BY m.usage_month, b.cost_kind;
