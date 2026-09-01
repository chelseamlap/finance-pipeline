-- Item-level and Simplifi monthly category spend, combined.
CREATE OR REPLACE VIEW `spending-pipeline.finance_pipeline.v_blended_monthly_category` AS
SELECT month, retailer, category, source, amount FROM `spending-pipeline.finance_pipeline.v_item_level_monthly_category`
UNION ALL
SELECT month, retailer, category, source, amount FROM `spending-pipeline.finance_pipeline.v_simplifi_monthly_category`
