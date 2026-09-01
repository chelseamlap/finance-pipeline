-- Simplifi spend, excluding anything already covered by item-level detail (no
-- double-counting), crosswalked onto the household category taxonomy where a
-- category overlaps with item-level purchases (see blended_category, computed in
-- finance_pipeline/normalize.py::blended_category_for_simplifi from
-- config/simplifi_blended_category_mapping.yaml). Anything without an explicit
-- rename passes through as its own Simplifi category name -- that's intentional,
-- not a gap: "Mortgage and Escrow" stays "Mortgage and Escrow".
--
-- retailer is populated from merchant_normalized (not NULL) so unmatched
-- non-itemized spend at a known merchant -- Costco gas, a Target run with no
-- item export -- can still be drilled into by retailer during validation.
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_simplifi_monthly_category` AS
WITH matched AS (
  SELECT DISTINCT matched_simplifi_transaction_id AS transaction_id
  FROM `spending-pipeline.finance_pipeline.retail_items`
  WHERE matched_simplifi_transaction_id IS NOT NULL AND matched_simplifi_transaction_id != ''
)
SELECT
  FORMAT_DATE('%Y-%m', DATE(posted_date)) AS month,
  merchant_normalized AS retailer,
  blended_category AS category,
  'simplifi' AS source,
  SUM(-amount) AS amount
FROM `spending-pipeline.finance_pipeline.transactions`
WHERE amount < 0
  AND spending_class NOT IN ('Transfer', 'Income', 'Excluded', 'Tax', 'Savings')
  AND transaction_id NOT IN (SELECT transaction_id FROM matched)
GROUP BY month, retailer, category
