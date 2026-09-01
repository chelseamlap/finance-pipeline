-- Same as v_monthly_spending_class_totals, wide/pivoted (one column per spending class):
--   SELECT * FROM v_monthly_spending_class_pivot ORDER BY month
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_monthly_spending_class_pivot` AS
SELECT * FROM (
  SELECT month, retailer, spending_class, amount FROM `spending-pipeline.finance_pipeline.v_monthly_spending_class_totals`
)
PIVOT (SUM(amount) FOR spending_class IN (
  'Fixed Required', 'Variable Required', 'Sinking', 'Discretionary', 'Reimbursable', 'Review'
))
ORDER BY month, retailer
