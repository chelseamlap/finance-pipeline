# Progress

Last updated: 2026-05-21

## Current State

The finance pipeline is now a working local-first CLI for parsing household finance exports and producing month-scoped reconciliation outputs.

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

Local Google auth is expected to use the service account key at:

```text
/Users/chelsea.lapepeikis/.config/finance-pipeline/spending-pipeline-service-account.json
```

## What Works Now

- Simplifi transaction CSV ingestion.
- OrderPro `.gsheet` ingestion through Google Sheets API.
- OrderPro Target, Costco, and Amazon folders.
- Amazon Order History Reporter order-level plus item-level exports in the same folder.
- Month-scoped exports under `data/processed/YYYY-MM/`.
- Firestore saved mappings and BigQuery analytics hooks are available when CLI flags are provided.
- Category rules are deterministic and layered: saved mapping, exact identifiers, exact descriptions, search overrides, broad keywords, retailer fallback, then `Unknown_Review`.
- Retail orders now match back to Simplifi using the retailer `source_grand_total` when available, so item-vs-retailer mismatches can still be reconciled to the posted transaction.
- Reconciliation detail now separates Simplifi truth (`simplifi_amount` / `simplifi_reconciled_total`), item-derived totals (`item_derived_total`), retailer/export totals (`retailer_source_grand_total`), and pairwise differences instead of forcing category totals to match.
- Same-order duplicate OrderPro item rows are reconciled before categorization/reconciliation; when `source_order_total` is available, the pipeline keeps the duplicate count that makes item subtotals closest to the retailer order subtotal.
- Simplifi matching is sign-aware: positive retailer charges no longer match Simplifi refunds by absolute value, while negative retailer refund totals can match positive Simplifi credits.

## Important Recent Commits

Latest pushed work on `main` includes:

```text
0cc0e9c Scope monthly reconciliation exports
dff6793 Improve monthly reconciliation matching
2a2281b Add reconciliation mismatch diagnostics
bbf4532 Apply source-backed component consistency
fd0a5b5 Refactor component matching as subset search
```

The component logic now treats order-level costs as a component array:

```text
tax, shipping, fee, discount
```

It searches all subsets of those components to see which combination ties item subtotal back to the retailer `source_grand_total`. If a source-backed subset matches, excluded components are zeroed out and recorded in `component_allocation_notes`. If nothing source-backed matches, the mismatch remains visible for review.

## Latest Validation

Test suite:

```text
28 passed
```

April rerun after the component subset refactor produced:

```text
retail_orders: 14
matched_orders: 14
unmatched_orders: 0
items_needing_review: 44
```

April reconciliation status:

```text
ok: 5
total_mismatch: 9
```

April mismatch diagnostics:

```text
single_item_price_or_adjustment_mismatch: 4
retailer_source_total_lower_than_item_components: 3
retailer_source_total_higher_than_item_components: 2
```

One Amazon order that previously mismatched is now explained and fixed by excluding shipping from allocated category spend. This was source-backed: excluding the shipping component made the item total tie to the charged total exactly.

## Current April Interpretation

The pipeline is now connecting the major pieces correctly:

- All April retail orders in the output match back to Simplifi.
- No April retail orders are unmatched.
- Remaining April issues are not matching failures; they are retailer-source/item-derived total explanation issues.
- Category totals are not force-balanced. They reflect item-derived totals after only source-backed component consistency fixes.

Remaining visible April issues:

- `9` total-mismatch orders still need explanation.
- `30` item rows are still `Unknown_Review`.
- `44` item rows need some kind of review because category and/or total mismatch issues remain.
- Simplifi still has unmatched Costco and Costco Gas transactions because April output has no itemized Costco retail rows.

## Start Here Tomorrow

1. Pull and confirm a clean tree:

```bash
cd /Users/chelsea.lapepeikis/Desktop/personal-repo/finance-pipeline
git pull --ff-only
git status -sb
```

2. Activate the environment and rerun tests:

```bash
source .venv/bin/activate
pytest -q
```

Expected:

```text
28 passed
```

3. Rerun April if needed:

```bash
python -m finance_pipeline.cli run-month --month 2026-04
python -m finance_pipeline.cli export --month 2026-04
```

4. Open these first:

```text
data/processed/2026-04/reconciliation_summary.csv
data/processed/2026-04/reconciliation_detail.csv
data/processed/2026-04/items_needing_review.csv
data/processed/2026-04/monthly_category_summary.csv
```

5. Sort `reconciliation_detail.csv` by `mismatch_diagnostic` and tackle the remaining classes in this order:

- `single_item_price_or_adjustment_mismatch`
- `retailer_source_total_lower_than_item_components`
- `retailer_source_total_higher_than_item_components`

Do not add balancing adjustments unless a source field explains the gap.

## Recommended Next Code Work

1. Add better mismatch diagnostics for the remaining classes.

Useful next diagnostics:

- possible duplicate item rows
- possible missing item row
- possible refund/return not represented at item level
- possible item price changed after order
- possible Target fee/tip/bag/deposit treatment
- possible Amazon split shipment or order group issue

2. Improve category coverage.

Start with `Unknown_Review` rows in April. Prefer rules in this order:

- exact SKU/ASIN/UPC
- exact normalized description
- search override for specific phrase combinations
- broad keyword only if safe

3. Investigate Costco April coverage.

Simplifi has April Costco and Costco Gas transactions, but current April retail item output does not have Costco item rows. Determine whether this is expected because receipts are missing, or whether the Costco source folder/sheets need loader/schema work.

## Useful Commands

Ingest individual sources:

```bash
python -m finance_pipeline.cli ingest --source simplifi --path data/raw/simplifi/
python -m finance_pipeline.cli ingest --source amazon_order_history_reporter --path data/raw/amazon/amazon_order_history_reporter/
python -m finance_pipeline.cli ingest --source orderpro --store target --path data/raw/orderpro/target/
python -m finance_pipeline.cli ingest --source orderpro --store costco --path data/raw/orderpro/costco/
python -m finance_pipeline.cli ingest --source orderpro --store amazon --path data/raw/orderpro/amazon/
```

Run month with Google state/analytics when ready:

```bash
python -m finance_pipeline.cli run-month \
  --month 2026-04 \
  --firestore-project spending-pipeline \
  --bigquery-project spending-pipeline \
  --bigquery-dataset finance_pipeline
```

Save a manual mapping to Firestore:

```bash
python -m finance_pipeline.cli save-mapping \
  --firestore-project spending-pipeline \
  --type description \
  --key "target:whole milk" \
  --category Groceries
```

## Principles To Preserve

- Do not overwrite raw exports.
- Do not commit raw exports, processed outputs, rejected debug CSVs, or credentials.
- Do not silently drop rows.
- Do not infer missing money.
- Do not force category totals to match transaction totals without source-backed evidence.
- Do not create categories dynamically.
- Do not use source retailer categories as household categories unless explicitly configured.
- Prefer review files and explicit mappings/rules over opaque automation.
