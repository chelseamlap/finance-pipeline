---
name: reconciliation-quality
description: Assess reconciliation match quality in finance-pipeline -- whether matched_simplifi_transaction_id pairings between item-level orders (Target/Costco/Amazon) and Simplifi transactions are actually correct, not just "some match happened within tolerance." Use after a pipeline run, or whenever asked to audit/review reconciliation quality, investigate the Shopping/retailer-level catch-all category, or check for swapped or missed order-to-transaction matches.
---

# Reconciliation Quality Assessment

A repeatable audit procedure for this repo's *other* quality axis — not "is the category right" (`categorization-quality` handles that), but "is `matched_simplifi_transaction_id` actually linking the correct order to the correct bank/card transaction." `reconcile()` (`finance_pipeline/reconcile.py`) pairs each item-level order to a Simplifi transaction using a date window plus amount tolerance — fuzzy enough that a close-but-wrong transaction can plausibly match instead of the right one, especially two same-week same-amount orders from the same retailer. Nothing in the pipeline itself checks whether a *correct* match is actually correct, only whether *some* match happened within tolerance. This skill is that check.

This produces a **dollar-ranked report of specific suspect matches and gaps** — it never edits `config/source_registry.yaml`'s tolerances/windows or writes to BigQuery itself. Only apply a fix (adjusting a tolerance, re-running, correcting a specific match) if the user confirms it in the same conversation.

## Before you start — the precondition that actually matters most

**Check whether the local Simplifi export fully covers the history you're about to audit, before trusting anything else.** `reconcile()` runs fresh against whatever's currently loaded locally; `BigQueryAnalyticsStore._merge_dataframe`'s `MERGE` then overwrites `matched_simplifi_transaction_id` for every item-level order in the run, including blanking a previously-correct match if this run's (possibly narrower) Simplifi data doesn't cover that order's true counterpart. This is not a hypothetical — it happened for real on 2026-10-22: a Simplifi export covering only the trailing 12 months silently wiped 242 of 242 pre-window orders' matches ($24,555.45) across multiple reruns, before a full-history re-export fixed it.

```bash
# Local Simplifi coverage:
.venv/bin/python -c "
import pandas as pd
df = pd.read_csv('data/raw/simplifi/<the current export>.csv')
d = pd.to_datetime(df['Date'], format='%b %d, %Y', errors='coerce')
print('local coverage:', d.min(), '->', d.max())
"
```

```sql
-- Compare against BigQuery's actual order history. If local coverage's min date
-- is later than this, a fresh run-period will blank out real matches for
-- everything older -- do NOT run the pipeline again until a fuller export is in
-- hand, and do NOT trust the current *_unmatched_* numbers for dates outside
-- local coverage until that's resolved.
SELECT MIN(transaction_date) FROM `spending-pipeline.finance_pipeline.retail_items`
```

If local coverage is narrower than the full order history, stop and say so explicitly before running any check below — findings from a narrowed rerun are not reconciliation-quality signal, they're this exact regression.

Also confirm there's a recent run to audit (`ls -t data/processed/runs/ | head -1`), same as `categorization-quality`.

## 1. Tolerance-edge matches

Current defaults (`config/source_registry.yaml`): `date_window_days: 5`, `amount_tolerance: 0.03` (accounting), `match_amount_tolerance: 0.05` (matching), `amazon_extended_date_window_days: 10`, `amazon_extended_date_min_amount: 10.00`.

A match sitting right at the edge of `match_amount_tolerance` (e.g. $0.04-$0.05 off, not exact) is far more likely to be coincidental than one that's exact. Query `reconciliation_detail.csv` (or `retail_items`/`transactions` directly) for matched orders where `item_vs_simplifi_difference` or `retailer_vs_simplifi_difference` is non-zero but within tolerance, ranked by `abs(difference)` descending:

```sql
SELECT r.retailer, r.order_id, r.transaction_date, r.matched_simplifi_transaction_id,
       r.retailer_source_grand_total, t.posted_date, -t.amount AS simplifi_amount,
       ROUND(r.retailer_source_grand_total - (-t.amount), 2) AS gap
FROM `spending-pipeline.finance_pipeline.retail_items` r
JOIN `spending-pipeline.finance_pipeline.transactions` t
  ON r.matched_simplifi_transaction_id = t.transaction_id
WHERE r.matched_simplifi_transaction_id != ''
  AND ABS(r.retailer_source_grand_total - (-t.amount)) > 0.00
ORDER BY ABS(r.retailer_source_grand_total - (-t.amount)) DESC
```

Also flag **near-misses that currently show as unmatched** — an order with no match whose `retailer_source_grand_total` is *just outside* `match_amount_tolerance` of a same-week Simplifi charge at the same retailer (e.g. a 10-15 cent gap). These are real purchases the tolerance is too tight to catch, not data errors — caught one for real on 2026-10-22: a $368.50 Target order vs. a $368.35 charge the next day, 15 cents outside the $0.05 tolerance.

## 2. The swap signature

The failure mode that actually matters: two orders/transactions in the same date window where each would be an equally good (or better) match for the *other's* counterpart, not just its own. Look for retailer/week clusters with 2+ orders and 2+ candidate transactions, and check whether the assignment the pipeline made is the minimum-total-distance pairing or not:

```sql
-- Orders from the same retailer within a few days of each other, both matched
-- (or one matched/one not) -- the setup for a plausible swap.
SELECT retailer, order_id, transaction_date, retailer_source_grand_total, matched_simplifi_transaction_id
FROM `spending-pipeline.finance_pipeline.retail_items`
WHERE retailer = '<retailer>'
ORDER BY transaction_date
```
Then pull the Simplifi-side candidates in the same window (`transactions` filtered by `merchant_normalized` and a `±5`/`±10`-day band) and manually check: does order A's amount more closely match transaction Y (currently assigned to order B) than transaction X it's actually assigned to? `_match_transaction` in `reconcile.py` sorts candidates by `(_amount_delta, _date_delta, _date)` and takes the best *remaining* one in file-processing order — it's a greedy assignment, not a global optimum, so an earlier-processed order can claim a transaction that would have been a better fit for a later one.

## 3. Coverage gaps vs. real matching failures — don't conflate them

Before treating an unmatched order or an unmatched Simplifi "Shopping"/retailer-level charge as a reconciliation bug, check whether item-level data for that period even exists. A charge from before the retailer's own export history starts is a coverage limitation, not a matching failure, and no tolerance/window change will fix it:

```sql
-- Earliest item-level data per retailer:
SELECT retailer, MIN(transaction_date) FROM `spending-pipeline.finance_pipeline.retail_items` GROUP BY retailer
```

Anything unmatched *before* that date for a given retailer: not a finding, just a documented limitation. Anything unmatched *after* that date deserves the full look — pull the Simplifi charge's date/amount and check for a same-retailer order within the window that has no match (see query in "Before you start" section above, or `reconciliation_detail.csv`'s `status = 'unmatched_transaction'` rows).

## 4. Multi-order weeks: verify no better fit was missed

For any retailer/week with more than one matched order, confirm no other unmatched (or differently-matched) Simplifi transaction in the same window would have been an equally good or better fit for any of them. This is the systematic version of the swap check in #2 — worth doing as a batch pass (group matched orders by retailer + ISO week, flag any week with 2+) rather than one retailer at a time once #2 has established the pattern is real.

## Reporting the findings

Lead with the local-coverage precondition result (pass/fail), then a dollar-ranked list: each finding names the specific order_id/transaction_id pair, the gap (amount and/or days), which check caught it, and whether it's a coverage limitation (no action possible), a tolerance-edge miss (candidate for a one-off manual fix or a tolerance adjustment — user's call, not a reflexive widen), or a suspected swap (needs the two counterpart transactions checked by hand before concluding). Don't change `config/source_registry.yaml`'s tolerances or touch BigQuery without the user confirming — same discipline as `categorization-quality` and `unknown-category-review`.
