-- Row-level counterpart to the monthly rollups: one row per item or unmatched
-- transaction, not aggregated. Connect directly in Google Sheets (Data -> Data
-- connectors -> BigQuery) and sort/filter by amount within a category to spot
-- miscategorization by eye.
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_spend_detail` AS
WITH item_rows AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE(transaction_date)) AS month,
    DATE(transaction_date) AS date,
    CONCAT('item_level:', retailer) AS source,
    retailer,
    household_category AS category,
    spending_class,
    item_description_raw AS description,
    allocated_total AS amount,
    category_confidence,
    category_rule_id AS category_signal,
    order_id AS record_id
  FROM `spending-pipeline.finance_pipeline.retail_items`
  WHERE retailer IN ('target', 'costco', 'amazon')
),
simplifi_rows AS (
  SELECT
    month,
    date,
    'simplifi' AS source,
    retailer,
    category,
    spending_class,
    description,
    amount,
    CAST(NULL AS STRING) AS category_confidence,
    category_signal,
    record_id
  FROM `spending-pipeline.finance_pipeline.v_unmatched_simplifi_spend`
)
SELECT * FROM item_rows
UNION ALL
SELECT * FROM simplifi_rows
