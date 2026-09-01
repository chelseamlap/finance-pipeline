#!/usr/bin/env bash
# Deploys the BigQuery views in this directory to spending-pipeline.finance_pipeline.
# Order matters: each view is CREATE OR REPLACE, and BigQuery validates a view's
# query against its referenced tables/views at creation time, so anything a view
# depends on must be (re)created first.
set -euo pipefail
cd "$(dirname "$0")"

VIEWS=(
  v_item_level_monthly_category
  v_unmatched_simplifi_spend
  v_simplifi_monthly_category
  v_blended_monthly_category
  v_monthly_category_totals
  v_monthly_spending_class_totals
  v_monthly_spending_class_pivot
  v_spend_detail
  v_category_mapping
)

for view in "${VIEWS[@]}"; do
  echo "Deploying ${view}..."
  bq query --use_legacy_sql=false < "${view}.sql"
done

echo "Done."
