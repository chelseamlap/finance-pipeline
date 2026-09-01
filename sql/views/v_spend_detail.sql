-- Row-level counterpart to the monthly rollups: one row per item or unmatched
-- transaction, not aggregated. Connect directly in Google Sheets (Data -> Data
-- connectors -> BigQuery) and sort/filter by amount within a category to spot
-- miscategorization by eye.
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_spend_detail` AS
WITH matched AS (
  SELECT DISTINCT matched_simplifi_transaction_id AS transaction_id
  FROM `spending-pipeline.finance_pipeline.retail_items`
  WHERE matched_simplifi_transaction_id IS NOT NULL AND matched_simplifi_transaction_id != ''
),
item_rows AS (
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
    FORMAT_DATE('%Y-%m', DATE(posted_date)) AS month,
    DATE(posted_date) AS date,
    'simplifi' AS source,
    merchant_normalized AS retailer,
    blended_category AS category,
    spending_class,
    merchant_raw AS description,
    -amount AS amount,
    CAST(NULL AS STRING) AS category_confidence,
    simplifi_category AS category_signal,
    transaction_id AS record_id
  FROM `spending-pipeline.finance_pipeline.transactions`
  WHERE amount < 0
    AND spending_class NOT IN ('Transfer', 'Income', 'Tax', 'Savings', 'Excluded')
    AND transaction_id NOT IN (SELECT transaction_id FROM matched)
)
SELECT * FROM item_rows
UNION ALL
SELECT * FROM simplifi_rows
