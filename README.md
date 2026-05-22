# Finance Pipeline

Deterministic local parsing for Simplifi transactions and item-level retail exports. The pipeline normalizes source files into canonical CSVs, categorizes item purchases with stable saved mappings and YAML rules, reconciles retail orders back to Simplifi transactions, and emits Google Sheets/dashboard-friendly monthly outputs.

Accuracy and repeatability are the priority. The pipeline does not silently infer, drop, or hide money: malformed rows are rejected with source context, missing required fields produce warnings, and reconciliation differences are surfaced for review.

## Source Strategy

- **Simplifi:** manual CSV export.
- **Amazon:** Amazon Order History Reporter CSVs first. If the same folder contains both order-level and item-level Reporter exports, the loader combines them so order totals can be allocated across item rows.
- **OrderPro:** generic adapter for Target, Costco, Amazon, or any supported retailer under `data/raw/orderpro/{store}/`. Full-year OrderPro sheets are expected and safe to rerun.
- **Costco:** Costco Receipt Downloader JSON fallback.
- **Target:** OrderPro export first; manual Target files are supported as a fallback.

## Folder Structure

```text
finance-pipeline/
  config/
    category_taxonomy.yaml
    merchant_rules.yaml
    retailer_schema_aliases.yaml
    simplifi_category_mapping.yaml
    source_registry.yaml
    spending_class_mapping.yaml
  data/
    raw/
      simplifi/
      amazon/
      costco/
      target/
      orderpro/
    processed/
    rejected/
  finance_pipeline/
  tests/
```

Monthly outputs are written to `data/processed/YYYY-MM/`. Raw exports, processed outputs, rejected rows, credentials, and other private data should stay uncommitted.

For real household runs, this repo can live locally while the durable state lives in Google Cloud:

- **Firestore:** operational state for saved mappings, record fingerprints, and run state.
- **BigQuery:** analytical tables for canonical transactions, retail items, reconciliation outputs, and reporting.
- **Google Sheets:** human review/output surface for mappings, review queues, and summaries.

## First Setup

Run these commands from the repo root:

```bash
cd /Users/chelsea.lapepeikis/Desktop/personal-repo/finance-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Expected test result is currently `25 passed`.

## Google Cloud Setup

The Google Cloud project name and ID are both:

```text
spending-pipeline
```

Install and initialize `gcloud` if needed, then select the project:

```bash
gcloud config set project spending-pipeline
```

Enable the APIs used by the pipeline:

```bash
gcloud services enable sheets.googleapis.com firestore.googleapis.com bigquery.googleapis.com
```

Create or reuse the local service account:

```bash
gcloud iam service-accounts create finance-pipeline-local \
  --display-name="Finance Pipeline Local"
```

Grant the roles needed for local pipeline runs:

```bash
gcloud projects add-iam-policy-binding spending-pipeline \
  --member="serviceAccount:finance-pipeline-local@spending-pipeline.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

gcloud projects add-iam-policy-binding spending-pipeline \
  --member="serviceAccount:finance-pipeline-local@spending-pipeline.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding spending-pipeline \
  --member="serviceAccount:finance-pipeline-local@spending-pipeline.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"
```

Create a local key file:

```bash
mkdir -p ~/.config/finance-pipeline

gcloud iam service-accounts keys create \
  ~/.config/finance-pipeline/spending-pipeline-service-account.json \
  --iam-account=finance-pipeline-local@spending-pipeline.iam.gserviceaccount.com
```

Point Application Default Credentials-compatible libraries at that key:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/finance-pipeline/spending-pipeline-service-account.json"
```

To make that persistent for new terminal windows, append the same export to `~/.zshrc`:

```bash
printf '\nexport GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/finance-pipeline/spending-pipeline-service-account.json"\n' >> ~/.zshrc
source ~/.zshrc
```

Verify the file path, but do not run the JSON file as a command:

```bash
echo "$GOOGLE_APPLICATION_CREDENTIALS"
test -f "$GOOGLE_APPLICATION_CREDENTIALS" && echo "credentials file found"
```

Share any Google Sheets or Drive folders that contain `.gsheet` shortcuts with this service account email:

```text
finance-pipeline-local@spending-pipeline.iam.gserviceaccount.com
```

The loader reads `.gsheet` shortcuts through the Google Sheets API. The local `.gsheet` file only contains a spreadsheet ID; the actual spreadsheet must be shared with the service account.

## Monthly Workflow

1. Refresh or export source files into the matching `data/raw/...` folder.
2. Run individual ingests when you want to inspect one source.
3. Run the reporting month.
4. Review reconciliation and category review files.
5. Add saved mappings or YAML rules for recurring items.
6. Commit only code/config/doc/test changes, never raw or processed household data.

Smoke-test individual sources:

```bash
python -m finance_pipeline.cli ingest --source simplifi --path data/raw/simplifi/
python -m finance_pipeline.cli ingest --source amazon_order_history_reporter --path data/raw/amazon/amazon_order_history_reporter/
python -m finance_pipeline.cli ingest --source amazon_order_history_exporter --path data/raw/amazon/amazon_order_history_exporter/
python -m finance_pipeline.cli ingest --source costco_receipt_downloader --path data/raw/costco/costco_receipt_downloader/
python -m finance_pipeline.cli ingest --source orderpro --store target --path data/raw/orderpro/target/
python -m finance_pipeline.cli ingest --source orderpro --store costco --path data/raw/orderpro/costco/
python -m finance_pipeline.cli ingest --source orderpro --store amazon --path data/raw/orderpro/amazon/
```

Run a month locally:

```bash
python -m finance_pipeline.cli run-month --month 2026-05
python -m finance_pipeline.cli export --month 2026-05
```

`run-month` means "produce outputs for this reporting month." It does not mean the raw imports are only that month. The loaders may read full-year files, which is expected for OrderPro. The export step filters monthly output files, reconciliation detail, unmatched files, and review queues back to the requested month.

Run a month while persisting state to Firestore and analytics to BigQuery:

```bash
python -m finance_pipeline.cli run-month \
  --month 2026-05 \
  --firestore-project spending-pipeline \
  --bigquery-project spending-pipeline \
  --bigquery-dataset finance_pipeline
```

The stable record identifiers and row fingerprints let full-year OrderPro exports be reprocessed without treating unchanged rows as new work.

## Category Rules And Saved Mappings

Edit `config/merchant_rules.yaml` for deterministic local categorization. Categorization priority is:

1. saved Firestore mapping when Firestore is used
2. exact SKU, ASIN, or UPC
3. exact normalized item description
4. search overrides for specific phrase combinations
5. broad keyword rules
6. retailer fallback
7. `Unknown_Review`

Categories must exist in `config/category_taxonomy.yaml`; new categories are never invented at runtime.

Saved mappings prevent repeated categorization work. If `target:whole milk` is saved as `Groceries`, future matching rows reuse that decision before YAML keyword matching or any future LLM categorization step.

Use search overrides for specific exceptions that should beat broad keyword rules. For example, broad `milk` can map to `Groceries`, while `la roche posay` plus `skin milk` can map to `Health_Personal_Care` or another intentional category.

Save a mapping directly to Firestore:

```bash
python -m finance_pipeline.cli save-mapping \
  --firestore-project spending-pipeline \
  --type description \
  --key "target:whole milk" \
  --category Groceries
```

## Reconciliation

Simplifi is the source of truth for posted financial transactions. Retail exports explain what was inside those transactions. If those two stories disagree, the pipeline preserves the disagreement and asks for review instead of inventing a balancing row.

The pipeline compares calculated order totals against source grand totals and matches retail orders to Simplifi transactions using:

- transaction date window, default `+/- 5` days
- merchant/retailer match
- accounting mismatch tolerance, default `$0.03`
- transaction match tolerance, default `$0.05`

The matching tolerance is intentionally separate from the accounting tolerance. A transaction can match with a tiny posted-vs-retailer drift, such as `$0.05`, while the exact drift remains visible in `retailer_vs_simplifi_difference`. Amazon also has a conservative extended date fallback, default `10` days, for orders of at least `$10.00`; this catches normal Amazon posting delays without auto-matching suspicious small digital or placeholder charges.

Orders whose item-derived total and retailer grand total are both zero are marked `no_bank_transaction_expected`. They are not matched to a Simplifi transaction, but they are excluded from unmatched-order review because no bank/card posting should exist.

For monthly rollups, `store_reconciliation_summary.csv` compares each retailer's matched Simplifi total to the item-derived retail total. The practical accuracy target is `within_5_percent_of_store_simplifi=true`: the reconciled item total is within 5% of that store's matched Simplifi total. Unmatched retail orders remain categorized and keep `unmatched_transaction` review labels, but they are separated from `reconciled_item_total` so sync gaps do not make the reconciled store accuracy metric fail.

Review these files first:

```text
data/processed/YYYY-MM/reconciliation_summary.csv
data/processed/YYYY-MM/store_reconciliation_summary.csv
data/processed/YYYY-MM/reconciliation_detail.csv
data/processed/YYYY-MM/unmatched_simplifi_transactions.csv
data/processed/YYYY-MM/unmatched_retail_orders.csv
data/processed/YYYY-MM/items_needing_review.csv
```

Reconciliation detail is intentionally layered because Simplifi is the source of truth for money that actually hit the bank or card. The main comparison columns are `simplifi_amount` for the signed Simplifi transaction amount, `simplifi_reconciled_total` for the comparable spend/refund total, `item_derived_total` for the sum of item rows after allocated components, and `retailer_source_grand_total` for the retailer/export order total. Pairwise differences are written as `item_vs_simplifi_difference`, `retailer_vs_simplifi_difference`, and `item_vs_retailer_difference`. Matching is sign-aware: retailer charges match Simplifi spending transactions, and retailer refunds/credits match Simplifi credits instead of matching by absolute value alone.

Before categorization and reconciliation, same-order duplicate OrderPro item rows are reconciled against retailer `source_order_total` when available. For each duplicate item group, the pipeline keeps the number of repeated rows that makes the item subtotal layer closest to the retailer order subtotal, while preserving rows that differ by quantity, price, subtotal, or source totals. If an OrderPro order has only one unique item kind and the exported placeholder subtotal cannot explain the retailer order subtotal, that item subtotal is set to `source_order_total` with a dedupe note; this handles Costco return/placeholder lines such as `/1899652` and low-value `VET. RX` rows.

For item rows, `item_subtotal_raw` preserves the exported line subtotal, `line_subtotal_derived` records the pipeline's best line subtotal, and `item_subtotal` is the active subtotal used for allocation and reconciliation. When an export appears to provide a per-unit subtotal for a multi-quantity item, for example quantity `2`, unit price `$6.99`, item total `$6.99`, the pipeline derives the active subtotal from `quantity * unit_price` and records `item_subtotal_derivation_notes`.

Amazon Order History Reporter item-level exports treat order-level `refund` values as separate negative adjustment rows instead of discounts on the purchased items. The adjustment rows use order ids like `<order_id>:refund`, keep `source_category_raw=refund-adjustment`, and remain reviewable/matchable on their own. This keeps the original order charge from being undercounted while still preserving refund activity for categorization and reconciliation.

When retailer source tax, shipping, fee, or discount exists only at the order level, the amount is allocated proportionally across positive item subtotals. The final row allocation absorbs penny rounding so the item-derived total reconciles to the retailer source order value within tolerance.

Before reporting category totals, the pipeline checks whether order-level components can coexist with the retailer charged total. Discounts are normalized to positive amounts to subtract, with normalization notes recorded in `component_allocation_notes`. If a retailer source component, such as shipping, is present in the export but excluding it makes item totals tie exactly to the charged total, that component is left out of `allocated_total` and the item row records `component_allocation_notes`. Reconciliation diagnostics separately expose component mismatches and `base_difference_after_components`, which flags item subtotal/base mismatches after known tax, shipping, fee, and discount components are accounted for.

If item-derived order totals still differ from the retailer charged total, the pipeline does not force category totals to match. Instead, `reconciliation_detail.csv` includes component totals and `mismatch_diagnostic` / `mismatch_basis` fields so the gap can be fixed from source-backed evidence, such as missing discounts, shipping treatment, tax allocation, split shipments, missing item rows, or duplicate item rows.

## Troubleshooting

- `ACCESS_TOKEN_SCOPE_INSUFFICIENT`: you are probably using user ADC from `gcloud auth application-default login`; use the service account key through `GOOGLE_APPLICATION_CREDENTIALS` instead.
- `The caller does not have permission`: share the actual Google Sheet or containing Drive folder with `finance-pipeline-local@spending-pipeline.iam.gserviceaccount.com`.
- `This app is blocked`: avoid the OAuth app flow for this local pipeline and use the service account setup above.
- `.gsheet` imports return zero rows: confirm the spreadsheet tabs have recognizable headers listed in `config/retailer_schema_aliases.yaml`.
- Rejected rows appear in logs: check whether they are true data rows or footer/summary rows. Real data issues should be fixed in aliases or loaders, not by editing output CSVs.
- Unknown categories: add exact identifiers, exact descriptions, or careful search overrides before broad keywords.

## What This Does Not Do

- It does not connect directly to banks, Simplifi, Amazon, Target, or Costco.
- It does not silently guess missing amounts.
- It does not infer missing money to force a reconciliation.
- It does not create categories outside the configured taxonomy.
- It does not treat retailer-provided categories as household categories by default.
- It does not overwrite raw exports.
- It is not a budgeting app, rules UI, or opaque AI categorizer.
