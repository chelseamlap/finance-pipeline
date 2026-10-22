---
name: categorization-quality
description: Assess categorization quality in finance-pipeline at both the item level (household_category on canonical_retail_items) and the transaction level (spending_class on canonical_transactions). Use after a pipeline run, or whenever asked to audit/review/assess categorization quality, find miscategorized spend, or check for money silently missing from totals.
---

# Categorization Quality Assessment

A repeatable audit procedure for this repo's two categorization layers — item-level `household_category` (driven by `config/merchant_rules.yaml`) and transaction-level `spending_class` (driven by `config/simplifi_category_mapping.yaml` + `config/spending_class_mapping.yaml`). Run it after any pipeline run where categorization or crosswalk config changed, or periodically (e.g. monthly) to catch drift.

This produces a **report with a prioritized, dollar-ranked punch list of specific config changes** — it does not edit `config/*.yaml` itself. Only apply a fix if the user confirms it in the same conversation; this project's whole design principle is "never silently force-categorize or hide money," and that applies to this skill's own output too.

## Before you start

Confirm there's a recent local run to audit: `ls -t data/processed/runs/ | head -1` for the review folder, and `ls data/processed/ | grep -E '^[0-9]{4}-[0-9]{2}$'` for the month range covered. If the newest run looks stale relative to `data/raw/` file timestamps, say so and suggest a fresh `finance-pipeline run-period` before auditing — findings from stale data are misleading.

All checks below read `data/processed/20*/canonical_retail_items.csv` and `data/processed/20*/canonical_transactions.csv` across every available month (glob, not just the latest), since materiality and drift only show up in aggregate. Use `.venv/bin/python` with `pandas` — it's already a pipeline dependency.

If BigQuery is loaded and reachable (`gcloud auth list` shows an active account), `spending-pipeline.finance_pipeline.v_spend_detail` already has this exact shape as a live view — one row per item/unmatched-transaction with `category`, `spending_class`, `category_confidence`, `category_signal`, and `amount` — and can shortcut most of the SQL-shaped checks below via `bq query`. It's also what a human eyeballs directly in Google Sheets between audits; this skill and that view are meant to be used together, not as alternatives to each other.

## 1. Item-level: `household_category`

**Coverage and confidence mix.** Group `canonical_retail_items.csv` rows by `category_confidence` (`exact_identifier`, `exact_description`, `search_override`, `category_prefix`, `keyword`, `retailer_fallback`, `unknown`) and sum `allocated_total` per bucket. Report:
- `Unknown_Review` total $ and row count, both in absolute terms and as % of total item-level spend. This is the headline coverage number.
- The `retailer_fallback` share specifically — a large or growing fallback bucket means item-level detail isn't actually informing categorization; the retailer name alone is doing the work. That's a signal to add `category_prefix_rules` or keyword rules, not a stable end state.
- Top 15 `Unknown_Review` rows by `allocated_total`, with `item_description_raw`, `retailer`, `order_id` — this is the direct input to new `exact_descriptions`/`search_overrides`/`keyword_rules` entries in `merchant_rules.yaml`. Sort by dollar impact, not frequency — one $200 unknown item matters more than twenty $2 ones.

**Rule-id detail.** Group by `category_rule_id` and sum `allocated_total` — this is `category_rule_coverage.csv`'s job already; use it if the latest run wrote one, otherwise recompute from the raw CSVs. A rule_id with an outsized dollar share relative to its apparent specificity (e.g. a single broad keyword rule accounting for a huge % of a category) is worth a second look — it may be over-matching.

**Keyword collisions with brand names — check every `keyword` match, not just low-confidence ones.** This is a distinct failure mode from low confidence: a keyword match reports as high-confidence even when the keyword itself is ambiguous, so it won't show up by scanning `retailer_fallback`/`keyword` share alone. For each `kw:*` rule in `merchant_rules.yaml`, sample the actual `item_description_raw` values it's matching (`WHERE category_rule_id = 'kw:<name>'`) and look for a keyword that's also a common word in an unrelated product line — this exact check caught `kw:grocery`'s `"apple"` keyword matching "Apple Magic Keyboard"/"Apple Magic Trackpad"/"Apple Magic Mouse" (~$429 of Amazon electronics wrongly landing in `Groceries`). The fix is `exclude_keywords` on the offending rule, not removing the keyword — `"apple"` is still correct for actual produce.

**Retailer fallbacks deserve the same skepticism as keywords.** A `retailer_fallbacks:` entry that defaults every uncategorized item from a retailer to one category is a strong, blanket assumption — verify it's still true rather than assuming it is. This caught a Costco `retailer_fallback: Groceries` silently miscategorizing $1,276.59 of general merchandise (a shed, patio furniture, Nerf toys, a board game, weed killer) that had no `category_label` from the receipt export — `category_prefix_rules` already covered every real labeled department, so the fallback was only ever catching the unlabeled leftovers, and "assume unlabeled Costco item = groceries" stopped being a safe assumption once the item mix broadened beyond food runs. Prefer routing genuinely unexplained items to `Unknown_Review` over a single-category default unless the retailer is truly single-category.

## 2. Transaction-level: `spending_class`

**The Review bucket is the primary signal.** As of the 2026-08-20 fix, unmapped Simplifi categories land in `spending_class == 'Review'` rather than being silently excluded — so this bucket is exactly the set of real spend not yet crosswalked. Group `canonical_transactions.csv` by `simplifi_category` where `spending_class == 'Review'` and `amount < 0`, sum by category. Each one is a candidate for a new entry in `config/simplifi_category_mapping.yaml` (or `config/spending_class_mapping.yaml`'s `administrative:` block if it's genuinely not spend, e.g. a transfer). Rank by dollar impact.

**Sanity-check the Excluded bucket in the other direction.** Group by `simplifi_category`/`merchant_normalized` where `spending_class == 'Excluded'` and `amount < 0`, sum by category. Every entry here should be explainable as a real transfer, credit-card payment, internal account movement, tax, or cash withdrawal (check `administrative:` in `config/spending_class_mapping.yaml` for the intentional list). Anything with real negative $ that ISN'T one of those is a money-hiding bug — this exact pattern hid ~$4,300 of real spend (daycare activities, ski passes, car repairs) and, separately, would have wrongly included a $336K mortgage refinance as spend if not caught. Flag anything unexplained here as a **high-priority** finding, not a nice-to-have.

## 3. Cross-cutting: the outlier-in-a-normally-small-category pattern

This one check caught a real bug this session: Simplifi auto-categorizes by merchant name, so a one-time large purchase from a normally-small-recurring merchant (e.g. a $1,618.92 Apple hardware purchase landing in "Subscriptions" alongside $2–85 App Store charges) skews a category that should be flat and predictable.

For both `household_category` (items) and `simplifi_category`/`spending_class` (transactions): compute each category's monthly $ series, then flag any `(month, category)` cell where a single row is more than ~50% of that cell's total AND the category's typical monthly total elsewhere is much smaller than that cell. Report the specific transaction/item, not just the category — the fix is usually a targeted `exact_description`/`search_override` rule or a manual note to recategorize in Simplifi itself, not a broad rule change.

## 4. Trend check (regression detection)

If more than one `data/processed/runs/<run_id>/` exists, compare this run's `Unknown_Review` $ and `Review`-class $ against the previous run's `run_summary.csv`/equivalent recomputation. A shrinking gap is progress; a growing one — especially after a `merchant_rules.yaml` or crosswalk edit — means something regressed and needs its own investigation before adding more rules on top.

## Reporting the findings

Lead with headline numbers (coverage %, $ in Review, $ flagged as possible money-hiding), then a **dollar-ranked punch list**: each item names the specific data pattern, the file to edit (`merchant_rules.yaml` / `simplifi_category_mapping.yaml` / `spending_class_mapping.yaml`), and the suggested change. Don't apply any of it without the user confirming — then, if changes are made, rerun `finance-pipeline run-period` and re-check the same numbers to confirm the fix worked and didn't regress anything else, the same way every categorization fix this session was verified before being called done.
