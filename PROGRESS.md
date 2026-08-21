# Progress

Last updated: 2026-08-20

## Current State

The pipeline is past first-run and running against real household data (2025-01 through 2026-08). Three sources, all plain CSV: Simplifi (manual export), `store-receipt-extract` (homegrown Chrome extension, Target + Costco), Amazon Order History Reporter (browser extension). See `README.md` for the full setup and monthly workflow, and `docs/adding-a-source.md` for wiring in a new source (Walmart is the anticipated next one, not yet built).

Google Cloud project: `spending-pipeline`. Auth is personal `gcloud`/Application Default Credentials, not a service account — no Firestore, no saved-mapping persistence layer. `config/merchant_rules.yaml` is the entire categorization mechanism.

BigQuery (`spending-pipeline.finance_pipeline`) is the durable, queryable store. Six views answer the recurring "how much did we spend on X each month" questions without double-counting item-level detail against Simplifi: `v_item_level_monthly_category`, `v_simplifi_monthly_category`, `v_blended_monthly_category`, `v_monthly_category_totals` (by household category), plus `v_monthly_spending_class_totals`/`v_monthly_spending_class_pivot` (by budget type: Fixed Required / Variable Required / Sinking / Discretionary / Reimbursable / Review).

## Recent Work (2026-08-19 through 2026-08-20)

- Got Target, Costco, and Amazon item-level data flowing end to end alongside Simplifi; fixed a Target gift-card overcounting bug and an Amazon Google-Sheets footer-row crash.
- Closed most of the `Unknown_Review` gap by adding `category_prefix_rules` that key off each retailer's own category taxonomy (Amazon's product categories, Costco's `category_label`) — this did more than keyword rules alone.
- Found and fixed a real money-hiding bug in `spending_class_for_category()`: unmapped Simplifi categories defaulted to `Excluded` rather than `Review`, silently dropping ~$4,300 of real spend (daycare activities, ski passes, car repairs) from every total. Also caught and correctly excluded a $336K mortgage refinance transaction that the same fix would otherwise have swept into "spending."
- Stood up BigQuery from scratch (`gcloud` install, auth, billing, dataset, the 4 views above). Along the way found and fixed a real bug in `BigQueryAnalyticsStore._json_ready`: pandas' newer string dtype silently reverts a mapped-in `None` back to `NaN` on column reassignment, which BigQuery's JSON parser rejects outright. Never caught before because this was the first real BigQuery run ever attempted on this codebase.
- Removed OrderPro (all 4 retailer folders) and the Firestore-backed saved-mapping/state-persistence layer entirely — neither was in real use. Deleted ~4 loader modules, `mappings.py`, `storage/firestore_store.py`, `storage/memory_store.py`, and every CLI command/flag tied to them (`save-mapping`, `export-mappings`, `import-reviewed-mappings`, `accept/reject-mapping-candidate`, `--firestore-project`, `--mapping-csv`, `--persist-record-state`, `--queue-mapping-candidates`). Test suite went from 88 to 46 tests, all still real coverage of what's actually used.
- Redesigned the spending-class taxonomy: renamed `One-Time Project` to `Sinking` (better matches a household that does frequent DIY/travel/repairs — none of it is actually one-time) and split `Auto & Transport` so routine gas stays `Variable Required` while `Service & Parts`/`Registration` route to `Sinking`. Also gave `Ski Passes`/`Education`/`Education:Tuition` a real home in `Sinking` instead of the generic `Review` default. Added `v_monthly_spending_class_totals`/`v_monthly_spending_class_pivot` BigQuery views. Sinking now runs ~$3,400/month across the full history — a real, previously-invisible budget line.

## Known Open Items

- **Target reconciliation is outside the 5% target for some recent months** (July 2026: 12% gap, driven by 1 unmatched retail order — `unmatched_retail_orders.csv`/`reconciliation_detail.csv` for that month have the specifics). Amazon and Costco are both comfortably within threshold. Worth a look next time reconciliation gets attention, not urgent.
- **`Kids:Kids Activities`, `Fitness:Gym`, `Financial`, `Auto & Transport:Tolls`** are still sitting in the generic `Review` spending class — small dollar amounts, genuinely ambiguous whether they're `Variable Required` or `Sinking`, left for a real decision rather than a guess.
- **CSV-only option for sharing with FIL** — explicitly deferred, not started.
- **Looker Studio dashboard** off the BigQuery views — recommended, not yet built.

## Where To Look

```text
data/processed/runs/<run_id>/review/run_summary.csv           month-by-month health check
data/processed/runs/<run_id>/review/category_review.csv       grouped category decisions needing a look
data/processed/runs/<run_id>/review/reconciliation_review.csv order-level mismatches, sorted by priority
data/processed/YYYY-MM/store_reconciliation_summary.csv       per-store accuracy vs. Simplifi
```

## Start Here In A New Session

```bash
git status -sb
source .venv/bin/activate
pytest -q   # expect 46 passed
```

Then run the normal monthly workflow from `README.md`.
