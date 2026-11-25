-- 0006: EA price sheet (contract prices) as reference data. Source: the Cost Management price sheet CSV.
-- Prices are per UnitOfMeasure, which can be a block such as '1000 Hours'; they are never converted.
-- base_price and market_price are NULL when the file leaves them blank (blank is unknown, never zero).
-- Re-importing a period replaces that period: the rows are keyed by (enrollment, effective_start).

CREATE TABLE price_sheet (
  enrollment VARCHAR NOT NULL,
  effective_start DATE NOT NULL,
  effective_end DATE,
  meter_id VARCHAR NOT NULL,
  meter_name VARCHAR,
  meter_type VARCHAR,
  meter_category VARCHAR,
  meter_subcategory VARCHAR,
  service_family VARCHAR,
  product VARCHAR,
  sku_id VARCHAR,
  product_id VARCHAR,
  part_number VARCHAR,
  meter_region VARCHAR NOT NULL,
  unit_of_measure VARCHAR NOT NULL,
  offer_id VARCHAR NOT NULL,
  price_type VARCHAR NOT NULL,
  term VARCHAR NOT NULL,
  currency VARCHAR NOT NULL,
  unit_price DECIMAL(28,10) NOT NULL,
  base_price DECIMAL(28,10),
  market_price DECIMAL(28,10),
  included_quantity DECIMAL(28,10),
  source_file VARCHAR NOT NULL
);

CREATE TABLE price_sheet_files (
  file_name VARCHAR NOT NULL,
  imported_at TIMESTAMP NOT NULL,
  bytes BIGINT NOT NULL,
  rows_loaded BIGINT NOT NULL,
  rows_rejected BIGINT NOT NULL
);
