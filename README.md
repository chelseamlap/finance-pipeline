# Finance Pipeline

Deterministic monthly parsing for Simplifi transactions and item-level retail exports. The pipeline normalizes source files into canonical CSVs, categorizes item purchases with stable YAML rules, reconciles totals, and emits Google Sheets/dashboard-friendly monthly outputs.

Accuracy and repeatability are the priority. The pipeline does not silently infer, drop, or hide money: malformed rows are rejected with source context, missing required fields produce warnings, and reconciliation differences are surfaced for review.

## Source Strategy

- **Simplifi:** manual CSV export.
- **Amazon:** Amazon Order History Reporter CSV first; Amazon Order History Exporter JSON/CSV fallback.
- **Costco:** Costco Receipt Downloader JSON first.
- **Target:** OrderPro export first; manual Target files are supported as a fallback.
- **OrderPro:** generic adapter for any supported retailer under `data/raw/orderpro/{store}/`.

## Folder Structure

```text
finance_pipeline/
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
    loaders/
    categorize.py
    reconcile.py
    export.py
  tests/
    fixtures/
```

Monthly outputs are written to `data/processed/YYYY-MM/`. Raw exports are never overwritten; rejected/debug files are written under `data/rejected/`.

## Setup

```bash
cd finance_pipeline
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Architecture

The pipeline has four intentionally simple stages:

1. **Load:** source-specific adapters read CSV, JSON, or XLSX files, apply configured schema aliases, and emit canonical rows.
2. **Normalize:** dates, merchants, amounts, identifiers, and text fields are converted into stable local formats.
3. **Categorize:** item-level retail rows are categorized only by YAML rules, in deterministic priority order.
4. **Reconcile and export:** order totals are checked against source totals, retail orders are matched to Simplifi, and monthly CSVs are emitted.

Canonical output files:

```text
canonical_transactions.csv
canonical_retail_items.csv
monthly_category_summary.csv
retailer_summary.csv
reconciliation_summary.csv
reconciliation_detail.csv
unmatched_simplifi_transactions.csv
unmatched_retail_orders.csv
items_needing_review.csv
category_rule_coverage.csv
```

## Monthly Workflow

1. Put source exports into the matching `data/raw/...` folder.
2. Ingest individual sources when you want to inspect them:

```bash
python -m finance_pipeline.cli ingest --source simplifi --path data/raw/simplifi/
python -m finance_pipeline.cli ingest --source amazon_order_history_reporter --path data/raw/amazon/amazon_order_history_reporter/
python -m finance_pipeline.cli ingest --source amazon_order_history_exporter --path data/raw/amazon/amazon_order_history_exporter/
python -m finance_pipeline.cli ingest --source costco_receipt_downloader --path data/raw/costco/costco_receipt_downloader/
python -m finance_pipeline.cli ingest --source orderpro --store target --path data/raw/orderpro/target/
```

3. Run the month:

```bash
python -m finance_pipeline.cli run-month --month 2026-05
python -m finance_pipeline.cli export --month 2026-05
```

## Adding an OrderPro Store

Create a folder like `data/raw/orderpro/walmart/` and place OrderPro order-level and item-level exports inside it. Then run:

```bash
python -m finance_pipeline.cli ingest --source orderpro --store walmart --path data/raw/orderpro/walmart/
```

The parser infers the retailer from `--store` or the folder name, detects order and item reports by columns, joins them when possible, and preserves OrderPro category fields as `source_category_raw`. OrderPro categories are not used as household categories unless a future explicit rule enables that.

## Adding Category Rules

Edit `config/merchant_rules.yaml`. Categorization is deterministic:

1. exact SKU, ASIN, or UPC
2. exact normalized item description
3. keyword rules
4. retailer fallback
5. `Unknown_Review`

Categories must exist in `config/category_taxonomy.yaml`; new categories are never invented at runtime.

## Reconciliation

The pipeline compares calculated order totals against source grand totals and matches retail orders to Simplifi transactions using:

- transaction date window, default `+/- 5` days
- merchant/retailer match
- amount tolerance, default `$0.03`

Review files include unmatched transactions, unmatched retail orders, reconciliation detail, and item rows needing review.

When source tax, shipping, fee, or discount exists only at the order level, the amount is allocated proportionally across positive item subtotals. The final row allocation absorbs penny rounding so the item sum reconciles to the source order value within tolerance.

## Reconciliation Philosophy

Simplifi is the source of truth for posted financial transactions. Retail exports explain what was inside those transactions. If those two stories disagree, the pipeline preserves the disagreement and asks for review instead of inventing a balancing row.

Money should be explainable from a source file, a deterministic transformation, and a reconciliation result. This is why the system keeps `file_source`, `import_batch_id`, source totals, original retailer categories, rule IDs, and review reasons in the output.

## Troubleshooting

- Check `data/rejected/` for files and rows that could not be parsed.
- Check `items_needing_review.csv` for unknown categories or reconciliation issues.
- Check `reconciliation_detail.csv` for order-level total differences.
- Confirm source files contain recognizable headers listed in `config/retailer_schema_aliases.yaml`.

## What This Does Not Do

- It does not connect directly to banks, Simplifi, Amazon, Target, or Costco.
- It does not silently guess missing amounts.
- It does not infer missing money to force a reconciliation.
- It does not create categories outside the configured taxonomy.
- It does not treat retailer-provided categories as household categories by default.
- It does not overwrite raw exports.
- It is not a budgeting app, rules UI, or opaque AI categorizer.
