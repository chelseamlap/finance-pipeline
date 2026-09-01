-- What item descriptions/merchants are mapping to what category, via which rule,
-- and how much money is behind it. Sourced from retail_items (already deduped by
-- the transactions/retail_items MERGE-upsert, so no run-duplication here).
-- Sort by total_amount desc and cross-check the big rows against
-- config/merchant_rules.yaml -- this is the QA surface for "is this rule putting
-- real money in the wrong category."
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_category_mapping` AS
SELECT
  retailer,
  category_rule_id,
  household_category AS category,
  spending_class,
  merchant_normalized,
  item_description_normalized,
  category_confidence,
  COUNT(*) AS item_count,
  SUM(allocated_total) AS total_amount
FROM `spending-pipeline.finance_pipeline.retail_items`
GROUP BY retailer, category_rule_id, category, spending_class, merchant_normalized, item_description_normalized, category_confidence
ORDER BY total_amount DESC
