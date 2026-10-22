-- Monthly totals by budget type (spending_class) instead of category, same
-- no-double-counting logic as v_blended_monthly_category. Long format:
-- (month, retailer, spending_class, amount).
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_monthly_spending_class_totals` AS
WITH item_level AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE(transaction_date)) AS month,
    retailer,
    spending_class,
    SUM(allocated_total) AS amount
  FROM `spending-pipeline.finance_pipeline.retail_items`
  WHERE retailer IN ('target', 'costco', 'amazon')
  GROUP BY month, retailer, spending_class
),
simplifi_level AS (
  SELECT month, retailer, spending_class, SUM(amount) AS amount
  FROM `spending-pipeline.finance_pipeline.v_unmatched_simplifi_spend`
  GROUP BY month, retailer, spending_class
)
SELECT month, retailer, spending_class, SUM(amount) AS amount
FROM (
  SELECT * FROM item_level
  UNION ALL
  SELECT * FROM simplifi_level
)
GROUP BY month, retailer, spending_class
