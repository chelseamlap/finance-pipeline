-- Required vs Periodic vs Optional grouping on top of v_spend_detail, for the
-- monthly expense summary (required-vs-optional breakdown + Sankey). Kept as a
-- durable view rather than an ad hoc query so the report is regenerable:
--   SELECT * FROM v_monthly_budget_group_totals WHERE month BETWEEN '2025-01' AND '2026-08'
--
-- Grouping:
--   Required  = Fixed Required + Variable Required (things that have to be paid,
--               monthly or near enough -- mortgage, childcare, groceries, utilities)
--   Periodic  = Sinking (real costs, but lumpy/irregular -- home projects, auto
--               registration/service, tuition, planned special spending)
--   Optional  = Discretionary (shopping, dining out, alcohol, entertainment)
--   Other     = Reimbursable (nets out once reimbursed) + Review (not yet
--               crosswalked -- small by design, see PROGRESS.md)
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_monthly_budget_group_totals` AS
SELECT
  month,
  retailer,
  category,
  spending_class,
  CASE spending_class
    WHEN 'Fixed Required' THEN 'Required'
    WHEN 'Variable Required' THEN 'Required'
    WHEN 'Sinking' THEN 'Periodic'
    WHEN 'Discretionary' THEN 'Optional'
    ELSE 'Other'
  END AS budget_group,
  description,
  amount
FROM `spending-pipeline.finance_pipeline.v_spend_detail`
