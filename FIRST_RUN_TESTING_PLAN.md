# First Run Testing Plan

This plan is for the first real run against household export files.

## 1. Confirm The Repo Is Healthy

```bash
cd /Users/chelsea.lapepeikis/Desktop/personal-repo/finance-pipeline
git status -sb
git pull --ff-only
```

Expected:

- Working tree is clean, except for intentional local raw data files.
- The `finance_pipeline/` package directory exists.
- `README.md`, `pyproject.toml`, `config/`, `data/`, and `tests/` exist at repo root.

If `finance_pipeline/` files show as deleted, stop and resolve that before testing.

## 2. Create Or Activate Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run baseline tests:

```bash
pytest -q
```

Expected:

```text
10 passed
```

## 3. Prepare Raw Data Carefully

Copy, do not move, source files into the matching folders:

```text
data/raw/simplifi/
data/raw/amazon/amazon_order_history_reporter/
data/raw/amazon/amazon_order_history_exporter/
data/raw/costco/costco_receipt_downloader/
data/raw/orderpro/target/
data/raw/orderpro/amazon/
data/raw/orderpro/walmart/
data/raw/orderpro/costco/
```

Keep original exports in `~/Documents/store exports` untouched.

Do not put private raw export files into git.

## 4. Smoke Test Each Source Individually

Run one source at a time and inspect row counts.

```bash
python -m finance_pipeline.cli ingest --source simplifi --path data/raw/simplifi/
python -m finance_pipeline.cli ingest --source amazon_order_history_reporter --path data/raw/amazon/amazon_order_history_reporter/
python -m finance_pipeline.cli ingest --source amazon_order_history_exporter --path data/raw/amazon/amazon_order_history_exporter/
python -m finance_pipeline.cli ingest --source costco_receipt_downloader --path data/raw/costco/costco_receipt_downloader/
python -m finance_pipeline.cli ingest --source orderpro --store target --path data/raw/orderpro/target/
```

For each run:

- Confirm the command completes.
- Confirm output row count is plausible.
- Check `data/rejected/` for rejected rows.
- If a whole file is rejected, inspect headers and update `config/retailer_schema_aliases.yaml`.

## 5. Run A Single Month

Pick one month with known good coverage, for example:

```bash
python -m finance_pipeline.cli run-month --month 2026-02
python -m finance_pipeline.cli export --month 2026-02
```

Expected output folder:

```text
data/processed/2026-02/
```

Expected files:

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

## 6. Review Reconciliation Before Trusting Categories

Start with money, not category labels.

Open:

```text
data/processed/YYYY-MM/reconciliation_summary.csv
data/processed/YYYY-MM/reconciliation_detail.csv
data/processed/YYYY-MM/unmatched_simplifi_transactions.csv
data/processed/YYYY-MM/unmatched_retail_orders.csv
```

Check:

- Are source order totals reconciling within `$0.03`?
- Are expected Amazon/Target/Costco transactions matched to Simplifi?
- Are unmatched Simplifi transactions actually unrelated to itemized exports?
- Are refunds or returns represented as negative rows where possible?

Do not fix reconciliation by editing output CSVs. Fix loaders, aliases, or raw source placement.

## 7. Review Items Needing Category Work

Open:

```text
data/processed/YYYY-MM/items_needing_review.csv
data/processed/YYYY-MM/category_rule_coverage.csv
```

For unknown items:

- Add exact SKU/ASIN/UPC rules when stable identifiers exist.
- Add exact normalized description rules for recurring known products.
- Add keyword rules only when the keyword is safe and specific.
- Do not add new household categories unless the taxonomy is intentionally changed.

After YAML edits:

```bash
pytest -q
python -m finance_pipeline.cli run-month --month YYYY-MM
```

## 8. Validate Google Sheets Outputs

Before connecting dashboards:

- Open `canonical_transactions.csv`.
- Open `canonical_retail_items.csv`.
- Confirm column names match README/canonical schema.
- Confirm amounts are signed and formatted as expected.
- Confirm `file_source` and `import_batch_id` are populated.
- Confirm `source_category_raw` is preserved but not used as final category.

## 9. Commit Only Code And Config Changes

Before committing:

```bash
git status -sb
```

Allowed to commit:

- Code changes
- Config/YAML changes
- Tests and fixtures that do not contain personal data
- README or progress docs

Do not commit:

- Real raw exports
- Processed monthly CSVs
- Rejected debug CSVs
- `.venv`
- `.DS_Store`

## 10. First Run Success Criteria

The first run is successful when:

- `pytest -q` passes.
- A chosen month produces all expected CSV outputs.
- Reconciliation differences are either zero or explicitly explained in review files.
- No rows disappear without either canonical output or rejected output.
- Unknown categories are visible in `items_needing_review.csv`.
- Any rule changes are deterministic YAML edits with tests when needed.
