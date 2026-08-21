---
name: unknown-category-review
description: Review Unknown_Review item-level rows with LLM judgment and propose (then, on confirmation, permanently apply) category rules in config/merchant_rules.yaml. Use when asked to review, categorize, or clear out unknown/uncategorized items, or to "run the LLM category review."
---

# Unknown Category Review

`config/merchant_rules.yaml` is the entire categorization mechanism (see README) — deterministic, no learned/persisted override layer. This skill doesn't add one. It's a faster way to *write* rules into that same file: instead of a human reading every `Unknown_Review` row and hand-writing YAML, Claude reads them, proposes categories using judgment, and — only on explicit confirmation — writes the accepted ones into `merchant_rules.yaml` directly. The override "sticks permanently" because it becomes an ordinary deterministic rule, indistinguishable from one a human wrote by hand. No database, no separate mapping store.

This is a human-in-the-loop tool, not a runtime categorizer — the pipeline itself stays deterministic. Never write to `merchant_rules.yaml` without the user confirming the specific proposals first.

## Procedure

**1. Pull and group.** Across `data/processed/20*/canonical_retail_items.csv`, filter `household_category == 'Unknown_Review'`, group by `item_description_normalized` (fall back to `item_description_raw`), and sum `allocated_total` per group — also carry `sku`/`asin`/`upc`, `retailer`, and row count. Sort by `abs(amount)` descending. This is the same grouping the `categorization-quality` skill's "Top 15 Unknown_Review rows" check already does; reuse its output if it ran recently.

**2. Propose, prioritizing rule-level fixes over one-off item fixes.** Work down the dollar-ranked list and, for each item, decide the category using judgment (product knowledge, the description text, the retailer, and — for Amazon — the `source_category_raw` product taxonomy string if present). Before proposing a one-off `exact_descriptions` entry, check whether the item is actually an instance of a **pattern already covered by an existing keyword rule that's just missing a term** — e.g. multiple distinct SKUs all being "Women's ... Shorts" when `kw:clothing` has `shirt`/`pants`/`jeans` but not `shorts`. A missing keyword fix clears the whole pattern (past and future purchases); a one-off `exact_descriptions` entry only fixes that one SKU. Prefer the rule-level fix whenever ≥2 distinct items in the batch share it.

Use `exact_identifiers` (keyed by `asin`/`sku`/`upc`) when a stable identifier exists and the item is a one-off — it's the most precise, least likely to have false positives. Use `exact_descriptions` for a single recurring item with no stable ID. Use a `keywords`/`exclude_keywords` addition on an existing `kw:*` rule for a genuine pattern. Only propose a brand-new `kw:*` rule if nothing existing fits.

Skip anything genuinely ambiguous rather than guessing — a bike part, a generic "organizer," a journal/notebook with no clear household-budget category. Leave those in `Unknown_Review`; say so explicitly in the report rather than silently omitting them.

**3. Present proposals for confirmation**, grouped by change type (new keyword-rule terms first, since they clear the most $ per line changed; then exact_descriptions; then exact_identifiers), each with: the pattern/item, $ impact, retailer, suggested category, and the exact YAML addition. Do not edit `config/merchant_rules.yaml` until the user confirms — accept all, accept a subset, or reject.

**4. Apply confirmed changes** directly to `config/merchant_rules.yaml`, following existing conventions: `rule_id` format (`sku:<value>`, `asin:<value>`, `upc:<value>`, `desc:<slug>`), alphabetical-ish grouping matching what's already there, no new categories outside `config/category_taxonomy.yaml`.

**5. Verify.** Rerun `finance-pipeline run-period` for the full history (BigQuery flags optional for a quick local check), then confirm: the target items now carry the expected `household_category` and `category_rule_id`, `Unknown_Review` shrank by roughly the proposed amount, and total item-level spend by category didn't move anywhere unexpected (spot-check a couple of categories that shouldn't have changed). Report before/after `Unknown_Review` $ and count. This mirrors the verification discipline used for every other categorization fix in this repo — a proposal isn't done until it's been run and checked, not just written.

## Notes from the first run (2026-08-21)

The Costco `retailer_fallback` and `apple`-keyword fixes (see `config/merchant_rules.yaml` history) both came from spotting a *pattern* behind several individually-small items rather than fixing them one at a time — the same instinct this skill should apply at step 2. Also: `config/merchant_rules.yaml`'s `kw:clothing` was missing `shorts` and `vest`, `kw:household` was missing drinkware/cookware terms (`tumbler`, `mug`, `skillet`), and `kw:health` was missing `serum`/`sunscreen`/`night cream`/`first aid` — all found by grouping real `Unknown_Review` data by product type rather than reading rows one at a time.
