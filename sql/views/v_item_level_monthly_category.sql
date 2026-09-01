-- Target/Costco/Amazon item-level detail by month/retailer/category.
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_item_level_monthly_category` AS
SELECT
  FORMAT_DATE('%Y-%m', DATE(transaction_date)) AS month,
  retailer,
  household_category AS category,
  CONCAT('item_level:', retailer) AS source,
  SUM(allocated_total) AS amount
FROM `spending-pipeline.finance_pipeline.retail_items`
WHERE retailer IN ('target', 'costco', 'amazon')
GROUP BY month, retailer, category
