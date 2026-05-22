# Progress

Last updated: 2026-05-22

## Current State

The finance pipeline is a working local-first CLI for parsing Simplifi transactions and item-level retail exports, reconciling retail orders back to posted Simplifi transactions, and producing month-scoped outputs under `data/processed/YYYY-MM/`.

Active checkout:

```text
/Users/chelsea.lapepeikis/Desktop/personal-repo/finance-pipeline
```

Remote:

```text
https://github.com/chelseamlap/finance-pipeline
```

Google Cloud project:

```text
spending-pipeline
```

Local Google auth should use the service account key at:

```text
/Users/chelsea.lapepeikis/.config/finance-pipeline/spending-pipeline-service-account.json
```

Simplifi is the source of truth for money that actually hit the bank/card. Retail exports explain what was inside those transactions. The practical accuracy target is per store: each store's reconciled item total should be within 5% of that store's matched Simplifi total.

## Current Pushed Checkpoint

Latest pushed code checkpoint before this documentation update:

```text
d386948 Improve monthly reconciliation accuracy
```

That batch included:

- Separate transaction match tolerance (`$0.05`) from accounting mismatch tolerance (`$0.03`).
- Amazon extended-date fallback for normal posting delays, with guardrails against suspicious small matches.
- True zero-dollar orders marked `no_bank_transaction_expected` instead of unmatched.
- Per-store reconciliation summary based on each store's matched Simplifi total, not a month-wide denominator.
- Unmatched retail orders remain categorized and labeled, but are excluded from the reconciled store accuracy metric.
- OrderPro duplicate collapse and placeholder handling for Costco return/odd rows.
- Multi-quantity OrderPro line subtotal derivation with `item_subtotal_raw`, `line_subtotal_derived`, and `item_subtotal_derivation_notes`.
- Sign-aware matching so retailer charges do not match Simplifi credits by absolute value.
- Amazon Order History Reporter refunds emitted as separate negative adjustment rows instead of discounts on purchased items.

## What Works Now

- Simplifi transaction CSV ingestion.
- OrderPro `.gsheet` ingestion through Google Sheets API.
- OrderPro Target, Costco, and Amazon folders.
- Amazon Order History Reporter order-level plus item-level exports in the same folder.
- Costco, Target, and Amazon month runs for January and February 2026.
- Month-scoped exports under `data/processed/YYYY-MM/`.
- Firestore saved mappings and BigQuery analytics hooks when CLI flags are provided.
- Layered deterministic categorization: saved mapping, exact identifiers, exact descriptions, search overrides, broad keywords, retailer fallback, then `Unknown_Review`.
- Reconciliation files that keep Simplifi truth, item-derived totals, retailer/export totals, and mismatch diagnostics separate.

## Latest Validation

Test suite after the current reconciliation changes:

```text
45 passed
```

January 2026 store reconciliation from `data/processed/2026-01/store_reconciliation_summary.csv`:

```text
retailer  matched_simplifi_total  reconciled_item_total  reconciled_gap  gap_pct  within_5_percent
amazon    726.36                  762.54                 36.18           4.98%    True
costco    640.49                  647.55                 7.06            1.10%    True
target    343.01                  343.43                 0.42            0.12%    True
```

February 2026 store reconciliation from `data/processed/2026-02/store_reconciliation_summary.csv`:

```text
retailer  matched_simplifi_total  reconciled_item_total  reconciled_gap  gap_pct  within_5_percent
amazon    238.74                  246.88                 8.14            3.41%    True
costco    1034.80                 1026.37                -8.43           0.81%    True
target    340.80                  353.70                 12.90           3.79%    True
```

Interpretation:

- January and February are now within the per-store 5% threshold for Amazon, Costco, and Target.
- Costco is much closer after duplicate collapse, placeholder handling, and store-level denominator changes.
- Amazon Order History Reporter is favored over OrderPro for Amazon where available.
- Amazon refunds are visible as separate negative adjustment rows, preserving both the original purchase and refund activity.
- Unmatched retail rows are still categorized and reviewable, but they no longer make a store fail the reconciled accuracy metric if Simplifi has no matching posted transaction.

## Important Output Files

Open these first for any month:

```text
data/processed/YYYY-MM/store_reconciliation_summary.csv
data/processed/YYYY-MM/reconciliation_summary.csv
data/processed/YYYY-MM/reconciliation_detail.csv
data/processed/YYYY-MM/unmatched_simplifi_transactions.csv
data/processed/YYYY-MM/unmatched_retail_orders.csv
data/processed/YYYY-MM/items_needing_review.csv
data/processed/YYYY-MM/monthly_category_summary.csv
```

The key checkpoint file is `store_reconciliation_summary.csv`. The pass/fail field to watch is:

```text
within_5_percent_of_store_simplifi
```

Use `reconciliation_detail.csv` when a store misses the threshold. Use `items_needing_review.csv` for category cleanup and source-backed mismatch review.

## Start Here In A New Chat

1. Pull and confirm the tree:

```bash
cd /Users/chelsea.lapepeikis/Desktop/personal-repo/finance-pipeline
git pull --ff-only
git status -sb
```

2. Activate the environment and run tests:

```bash
source .venv/bin/activate
pytest -q
```

Expected:

```text
45 passed
```

3. Inspect the known-good January and February store summaries:

```bash
cat data/processed/2026-01/store_reconciliation_summary.csv
cat data/processed/2026-02/store_reconciliation_summary.csv
```

4. Run the next month when ready:

```bash
python -m finance_pipeline.cli run-month --month 2026-03
python -m finance_pipeline.cli export --month 2026-03
```

5. If using Google state/analytics:

```bash
python -m finance_pipeline.cli run-month \
  --month 2026-03 \
  --firestore-project spending-pipeline \
  --bigquery-project spending-pipeline \
  --bigquery-dataset finance_pipeline
```

## Recommended Next Work

1. Run March 2026, then April 2026, and check `store_reconciliation_summary.csv` for each store.

2. Improve category coverage from `Unknown_Review` rows. Prefer stable rules in this order:

- exact SKU/ASIN/UPC
- exact normalized description
- search override for specific phrase combinations
- broad keyword only when safe

3. Add or save mappings for recurring household items so categorization is not reinvented. Example:

```bash
python -m finance_pipeline.cli save-mapping \
  --firestore-project spending-pipeline \
  --type description \
  --key "target:whole milk" \
  --category Groceries
```

4. If a store falls outside 5%, inspect in this order:

- `store_reconciliation_summary.csv` for the store-level gap
- `reconciliation_detail.csv` filtered to that retailer
- `unmatched_simplifi_transactions.csv` for Simplifi sync/import gaps
- `unmatched_retail_orders.csv` for retail orders that did not post or did not match
- `items_needing_review.csv` for category and mismatch notes

5. Keep treating mismatches as evidence to explain, not numbers to force. Prefer explicit review labels, source-backed component logic, and visible adjustment rows over hidden balancing.

## Design Principles To Preserve

- Simplifi is the financial source of truth.
- Retail exports are itemization evidence, not the bank ledger.
- The 5% target is per store against that store's matched Simplifi total.
- Unmatched retail can still be categorized, but it must remain visibly unmatched.
- Amazon refunds are separate negative adjustment rows, not discounts against purchased item rows.
- Full-year OrderPro files are expected and safe to rerun; stable identifiers and fingerprints prevent unchanged rows from becoming new work.
- Saved mappings should always beat broad category rules and any future LLM categorization.
- Do not commit raw exports, processed household data, rejected rows, or credentials.
