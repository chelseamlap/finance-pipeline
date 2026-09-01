-- Monthly totals per household category. The one to query day-to-day for
-- "how much did we spend on X":
--   SELECT month, amount FROM v_monthly_category_totals WHERE category = 'Groceries' ORDER BY month
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_monthly_category_totals` AS
SELECT month, retailer, category, SUM(amount) AS amount
FROM `spending-pipeline.finance_pipeline.v_blended_monthly_category`
GROUP BY month, retailer, category
