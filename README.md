# Finance Pipeline

Deterministic local parsing for Simplifi transactions and item-level retail exports. The pipeline normalizes source files into canonical CSVs, categorizes item purchases with YAML rules, reconciles retail orders back to Simplifi transactions, and loads the result into BigQuery for querying.

Accuracy and repeatability are the priority. The pipeline does not silently infer, drop, or hide money: malformed rows are rejected with source context, missing required fields produce warnings, and reconciliation differences are surfaced for review.

## Source Strategy

Three real sources, all plain CSV:

- **Simplifi:** manual CSV export, dropped into `data/raw/simplifi/`. Simplifi's export has no stable transaction ID, so **replace** the file on each refresh rather than adding a second one alongside it — leaving both in the folder double-counts every transaction in the overlapping date range.
- **Target + Costco, via `store-receipt-extract`:** a homegrown Chrome extension (separate repo) that exports paired `orders_<retailer>_*.csv` / `order_items_<retailer>_*.csv` files into `data/raw/store_receipt_extract/`. This loader dedupes same-order duplicate exports by filename timestamp on its own, so it's safe to leave old exports in the folder if you want — the latest one for a given order wins.
- **Amazon, via Amazon Order History Reporter:** a browser extension that exports paired order-level and item-level CSVs into `data/raw/amazon/amazon_order_history_reporter/`. When both are present the loader allocates order totals across item rows; it falls back to order-level-only or a generic parse if the item-level export isn't there.

Adding a fourth source (e.g. Walmart) later? See `docs/adding-a-source.md`.

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
      amazon/amazon_order_history_reporter/
      store_receipt_extract/
    processed/
    rejected/
  docs/
    adding-a-source.md
  finance_pipeline/
  tests/
```

Monthly outputs are written to `data/processed/YYYY-MM/`. Raw exports, processed outputs, rejected rows, credentials, and other private data should stay uncommitted (see `.gitignore`).

BigQuery holds the durable, queryable copy of everything — project `spending-pipeline`, dataset `finance_pipeline`. The per-month local CSVs are a secondary debugging artifact, not the source of truth once BigQuery is loaded.

## First Setup

```bash
cd finance-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Expected test result is currently `46 passed`.

## Google Cloud Setup

The project name and ID are both `spending-pipeline`. This uses your personal Google account via `gcloud`, not a service account — simpler for a single-user local pipeline.

```bash
brew install --cask gcloud-cli   # if gcloud isn't already installed
gcloud auth login                 # browser login, for the gcloud CLI itself
gcloud config set project spending-pipeline
gcloud services enable bigquery.googleapis.com
gcloud auth application-default login   # browser login, for the Python client library
```

BigQuery requires a billing account attached to the project even to stay within the free tier (10GB storage / 1TB queries per month free — this dataset is a few MB, so expect $0/month in practice). Set one up once at `console.cloud.google.com/billing` and link it to `spending-pipeline` if `gcloud billing projects describe spending-pipeline` shows `billingEnabled: false`.

Query views live in the `finance_pipeline` dataset:

- `v_item_level_monthly_category` — Target/Costco/Amazon item-level detail by month/retailer/category.
- `v_simplifi_monthly_category` — Simplifi spend, excluding anything already covered by item-level detail (no double-counting) and crosswalked onto the household category taxonomy.
- `v_blended_monthly_category` — the two combined.
- `v_monthly_category_totals` — monthly totals per household category; the one to query day-to-day for "how much did we spend on X", e.g.:

```sql
SELECT month, amount FROM `spending-pipeline.finance_pipeline.v_monthly_category_totals`
WHERE category = 'Groceries' ORDER BY month
```

Same pattern, rolled up by budget type instead of category — built from each row's `spending_class` on both the item and transaction tables, same no-double-counting logic:

- `v_monthly_spending_class_totals` — long format, `(month, spending_class, amount)`.
- `v_monthly_spending_class_pivot` — wide format, one column per spending class, e.g.:

```sql
SELECT * FROM `spending-pipeline.finance_pipeline.v_monthly_spending_class_pivot` ORDER BY month
```

For simple visuals on top of any of these, point Looker Studio at the views directly — no code needed.

## Monthly Workflow

1. Re-export Simplifi (replace, don't append — see Source Strategy above), and refresh Target/Costco/Amazon exports if there's new purchase activity.
2. Run the full history through `run-period`, not just the new month — it's idempotent (BigQuery upserts by `transaction_id`/`item_id`), so there's no incremental-run bookkeeping to think about:

```bash
finance-pipeline run-period \
  --start-month 2025-01 \
  --end-month 2026-08 \
  --bigquery-project spending-pipeline \
  --bigquery-dataset finance_pipeline \
  --bigquery-location US
```

3. Check the consolidated review output it prints, under `data/processed/runs/<run_id>/review/`:
   - `run_summary.csv` — month-by-month health check.
   - `category_review.csv` — grouped category decisions needing a look, with sample descriptions and impact.
   - `reconciliation_review.csv` — order-level mismatches sorted by review priority.
4. Add rules to `config/merchant_rules.yaml` for anything recurring that landed in `Unknown_Review` or came out miscategorized.
5. Query BigQuery for the numbers.
6. Commit only code/config/doc/test changes, never raw or processed household data.

Smoke-test one source in isolation:

```bash
finance-pipeline ingest --source simplifi --path data/raw/simplifi/
finance-pipeline ingest --source amazon_order_history_reporter --path data/raw/amazon/amazon_order_history_reporter/
finance-pipeline ingest --source store_receipt_extract --path data/raw/store_receipt_extract/
```

`store_receipt_extract` handles the Chrome extension CSV shape directly, including `order_channel`, `category_label`, and Target adjustment columns. If a Target item row has no item name, the loader creates a fallback description from retailer, SKU, and category and flags the row for review. If every item in a Target order is missing line totals but the order total is present, the loader evenly allocates the order total across the items and flags those rows for review instead of dropping the spend.

Run a single month locally, without touching BigQuery:

```bash
finance-pipeline run-month --month 2026-05
finance-pipeline export --month 2026-05
```

`run-month` means "produce outputs for this reporting month." It does not mean the raw imports are only that month — loaders may read full-year files. The export step filters monthly output files, reconciliation detail, unmatched files, and review queues back to the requested month.

## Category Rules

Edit `config/merchant_rules.yaml` for categorization — this is the entire categorization mechanism; there's no learned/persisted override layer sitting in front of it. Categorization priority is:

1. exact SKU, ASIN, or UPC
2. exact normalized item description
3. search overrides for specific phrase combinations
4. `category_prefix_rules` matches against the retailer's own `source_category_raw` (for example Amazon's `Clothing, Shoes & Jewelry›...` export category, or Costco's `category_label`)
5. broad keyword rules
6. retailer fallback
7. `Unknown_Review`

Categories must exist in `config/category_taxonomy.yaml`; new categories are never invented at runtime.

Use search overrides for specific exceptions that should beat broad keyword rules. For example, broad `milk` can map to `Groceries`, while `la roche posay` plus `skin milk` can map to `Health_Personal_Care` or another intentional category.

### Spending class (budget type)

For Simplifi's own transaction categories (not item-level retail categorization), `config/simplifi_category_mapping.yaml` normalizes raw category strings onto a `NN Label:Sub` shape, and `config/spending_class_mapping.yaml` maps the leading two-digit prefix — plus the household categories above, for item-level rows — onto a spending class:

| Prefix | Spending class | What lives there |
|---|---|---|
| `01` | Fixed Required | Same amount every period, contractually required: subscriptions, insurance, debt payments, daycare. |
| `02` | Variable Required | Necessary but fluctuates: groceries, routine gas fill-ups, utilities, household consumables, personal care. |
| `03` | Discretionary | Flexible, could be cut without real hardship: restaurants, alcohol, clothing, entertainment. |
| `04` | Reimbursable | Work expenses expected to come back as income. |
| `05` | Sinking | Irregular but recurring in aggregate — budget for it annually rather than expecting it flat month to month: home improvement/DIY, gifts, travel, car repairs/registration, annual passes, tuition. |

`Sinking` is deliberately not called "one-time" — for a household that does frequent DIY/home projects, car repairs, and travel, none of that is truly one-off, it's just lumpy. The distinction from `Variable Required` is planning horizon: variable-required spend is week-to-week and inelastic; sinking spend is annual-ish and worth setting aside for ahead of time. A single Simplifi category can split across two spending classes when the real distinction is planning horizon, not the category itself — see `Auto & Transport` in `config/simplifi_category_mapping.yaml`: `Gas & Fuel` stays `Variable Required`, but `Service & Parts` and `Registration` route to `Sinking`.

A category with no explicit mapping falls to `Review` rather than being silently excluded from totals — real spend should never disappear because a Simplifi category string doesn't have a rule yet. Query either rollup with `v_monthly_spending_class_totals`/`v_monthly_spending_class_pivot` (see BigQuery Setup above).

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

For item rows, `item_subtotal_raw` preserves the exported line subtotal, `line_subtotal_derived` records the pipeline's best line subtotal, and `item_subtotal` is the active subtotal used for allocation and reconciliation. When an export appears to provide a per-unit subtotal for a multi-quantity item, for example quantity `2`, unit price `$6.99`, item total `$6.99`, the pipeline derives the active subtotal from `quantity * unit_price` and records `item_subtotal_derivation_notes`.

Amazon Order History Reporter item-level exports treat order-level `refund` values as separate negative adjustment rows instead of discounts on the purchased items. The adjustment rows use order ids like `<order_id>:refund`, keep `source_category_raw=refund-adjustment`, and remain reviewable/matchable on their own. This keeps the original order charge from being undercounted while still preserving refund activity for categorization and reconciliation.

When retailer source tax, shipping, fee, or discount exists only at the order level, the amount is allocated proportionally across positive item subtotals. The final row allocation absorbs penny rounding so the item-derived total reconciles to the retailer source order value within tolerance.

Before reporting category totals, the pipeline checks whether order-level components can coexist with the retailer charged total. Discounts are normalized to positive amounts to subtract, with normalization notes recorded in `component_allocation_notes`. If a retailer source component, such as shipping, is present in the export but excluding it makes item totals tie exactly to the charged total, that component is left out of `allocated_total` and the item row records `component_allocation_notes`. Reconciliation diagnostics separately expose component mismatches and `base_difference_after_components`, which flags item subtotal/base mismatches after known tax, shipping, fee, and discount components are accounted for.

If item-derived order totals still differ from the retailer charged total, the pipeline does not force category totals to match. Instead, `reconciliation_detail.csv` includes component totals and `mismatch_diagnostic` / `mismatch_basis` fields so the gap can be fixed from source-backed evidence, such as missing discounts, shipping treatment, tax allocation, split shipments, missing item rows, or duplicate item rows.

## Troubleshooting

- `.gsheet` imports return zero rows: the loader can read Google Sheets shortcut files through the Sheets API if a source ever needs it (currently unused — all three real sources are plain CSV), but the spreadsheet needs recognizable headers listed in `config/retailer_schema_aliases.yaml`.
- Rejected rows appear in logs: check whether they are true data rows or footer/summary rows (e.g. a Google Sheets `=SUBTOTAL(...)` export artifact). Real data issues should be fixed in aliases or loaders, not by editing output CSVs.
- Unknown categories: add exact identifiers, exact descriptions, or careful search overrides before broad keywords.
- BigQuery `BadRequest`/JSON parse errors on load: check for `NaN`/`Infinity` sneaking into a payload — see the fix in `finance_pipeline/storage/bigquery_store.py` for the pandas dtype gotcha that caused this once already.

## What This Does Not Do

- It does not connect directly to banks, Simplifi, Amazon, Target, or Costco.
- It does not silently guess missing amounts.
- It does not infer missing money to force a reconciliation.
- It does not create categories outside the configured taxonomy.
- It does not treat retailer-provided categories as household categories by default.
- It does not overwrite raw exports.
- It does not persist a learned/saved category-mapping layer — `config/merchant_rules.yaml` is the entire categorization mechanism, edited by hand.
- It is not a budgeting app, rules UI, or opaque AI categorizer.
