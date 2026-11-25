-- 0005: subscription display names. Facts only carry the subscription id; names come from the export's
-- SubscriptionName column and are stored once per id (latest seen wins; a rename is a display change
-- only, it never alters cost figures). Safe to rebuild: run `azfinops-names <csv files>`.

CREATE TABLE dim_subscription (
  subscription_id VARCHAR PRIMARY KEY,
  subscription_name VARCHAR NOT NULL,
  name_variants INTEGER NOT NULL,       -- distinct non-blank names seen for this id in the harvested files
  updated_at TIMESTAMP NOT NULL
);
