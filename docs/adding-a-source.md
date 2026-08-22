# Adding a new source

Worked example: adding Walmart. The same steps apply to any new retail export or transaction feed.

## 1. Decide the output type

Every source produces either:

- **`transactions`** — bank/card-level postings, one row per transaction. Only Simplifi does this today. You will not need this for a new retailer.
- **`retail_items`** — itemized order/receipt lines. This is what Walmart, or any other retailer, would be.

## 2. Pick a loading strategy

**If Walmart's export is a plain CSV with recognizable columns** (order id, item name, quantity, price, totals), you don't need a custom loader — reuse the generic path:

- `finance_pipeline/loaders/retail_common.py::load_retail_items()` already handles parsing, identity, subtotal derivation, and order-amount allocation for any CSV that can be mapped onto the canonical column names.
- Add a column-alias block to `config/retailer_schema_aliases.yaml` under `retail_item:` (or a new named group if Walmart's headers collide with another source's aliases) so Walmart's actual column headers — whatever they're called in the export — map onto: `order_id`, `receipt_id`, `transaction_date`, `merchant_raw`, `item_description_raw`, `sku`/`asin`/`upc`, `quantity`, `unit_price`, `item_subtotal`, `item_discount`, `allocated_tax`/`shipping`/`fee`, `source_order_total`, `source_tax_total`, `source_discount_total`, `source_shipping_total`, `source_fee_total`, `source_grand_total`, `source_category_raw`.
- Write a loader module that's just a thin wrapper:

  ```python
  # finance_pipeline/loaders/walmart.py
  from __future__ import annotations
  from pathlib import Path
  from .retail_common import load_retail_items

  def load(path: Path, import_batch_id: str, store: str | None = None):
      return load_retail_items(path, import_batch_id, source_adapter="walmart", retailer="walmart")
  ```

**If the export needs bespoke parsing** (paired order+item files, JSON, weird per-row logic, multiple retailers sharing one adapter) — write a full custom loader. `finance_pipeline/loaders/store_receipt_extract.py` and `finance_pipeline/loaders/amazon_order_history_reporter.py` are the reference implementations: both build a `CanonicalRetailItem` per row directly (see `finance_pipeline/models.py::CanonicalRetailItem` for the full field list) rather than going through `load_retail_items`.

Either way, the identity of a retail item row is derived from `(retailer, source_owner, order_id, receipt_id, item_key, transaction_date, quantity, allocated_total)` (`finance_pipeline/identity.py::retail_identity_parts`) — a stable `order_id` per export is what makes reruns and re-exports safe rather than creating duplicate rows.

## 3. Register the source

Add an entry to `config/source_registry.yaml`:

```yaml
walmart:
  loader: finance_pipeline.loaders.walmart
  output: retail_items
  retailer: walmart
  default_path: data/raw/walmart/
```

This is the only wiring needed — `run-period`/`run-month` iterate the registry automatically, no other code change required to have it participate in a normal run.

Create the raw folder with a `.gitkeep` so it exists in git without committing real data:

```bash
mkdir -p data/raw/walmart
touch data/raw/walmart/.gitkeep
```

(`.gitignore` already excludes everything under `data/raw/**/*` except directory structure and `.gitkeep` files.)

## 4. Leverage the retailer's own categories, if it has them

If Walmart's export includes its own product category/department field, map it onto `source_category_raw` in the alias step above, then add entries to `category_prefix_rules:` in `config/merchant_rules.yaml` — substring matches against `source_category_raw`, checked before broad keyword rules. See the existing Amazon (`"Grocery & Gourmet Food": Groceries`) and Costco (`"Dry Grocery": Groceries`) entries for the pattern. This is high-leverage: it's usually a bigger accuracy win than adding more keyword rules.

## 5. Handle overlap with an existing source, if any

If Walmart could ever produce the same `(retailer, order_id)` as another loader (unlikely unless you're mid-migration from one export tool to another), add a priority entry to `SOURCE_PRIORITY` in `finance_pipeline/dedupe.py` so `dedupe_retail_items` knows which adapter wins.

## 6. Test it

Follow the pattern in `tests/test_store_receipt_extract.py`: write inline CSV fixtures with `tmp_path`, call the loader directly, assert on the resulting DataFrame's `retailer`, `source_adapter`, totals, and any edge cases (missing item names, missing line totals, duplicate exports). Add a case to `finance_pipeline/source_dates.py::_alias_groups` only if the new source needs its own alias group for source-date detection — otherwise the generic `retail_item` fallback already covers it.

## 7. Smoke-test, then run for real

```bash
finance-pipeline ingest --source walmart --path data/raw/walmart/
```

Inspect the output CSV before wiring it into a full `run-period`. Once it looks right, run the normal monthly workflow (see README) and check `data/processed/runs/<run_id>/review/category_review.csv` for anything from Walmart landing in `Unknown_Review` — that's your signal for what still needs a rule in `merchant_rules.yaml`.
