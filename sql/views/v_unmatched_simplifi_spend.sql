-- Row-level Simplifi spend not already covered by item-level detail (no
-- double-counting) and not an administrative flow (transfer/income/tax/savings/
-- excluded). Single source of truth for the "matched" join + exclusion filter
-- that v_simplifi_monthly_category, v_monthly_spending_class_totals, and
-- v_spend_detail all need identically -- previously each defined its own copy,
-- which is exactly how the household-category crosswalk drifted out of sync
-- across views in the first place.
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_unmatched_simplifi_spend` AS
WITH matched AS (
  SELECT DISTINCT matched_simplifi_transaction_id AS transaction_id
  FROM `spending-pipeline.finance_pipeline.retail_items`
  WHERE matched_simplifi_transaction_id IS NOT NULL AND matched_simplifi_transaction_id != ''
)
SELECT
  FORMAT_DATE('%Y-%m', DATE(posted_date)) AS month,
  DATE(posted_date) AS date,
  merchant_normalized AS retailer,
  blended_category AS category,
  spending_class,
  merchant_raw AS description,
  -amount AS amount,
  simplifi_category AS category_signal,
  transaction_id AS record_id
FROM `spending-pipeline.finance_pipeline.transactions`
WHERE amount < 0
  AND spending_class NOT IN ('Transfer', 'Income', 'Tax', 'Savings', 'Excluded')
  AND transaction_id NOT IN (SELECT transaction_id FROM matched)
