from __future__ import annotations

from pathlib import Path

import pandas as pd

from .normalize import load_yaml, normalize_text, spending_class_for_retail_category


def taxonomy() -> set[str]:
    return set(load_yaml("category_taxonomy.yaml").get("categories", []))


def categorize_items(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if df.empty:
        return df, pd.DataFrame()
    rules = load_yaml("merchant_rules.yaml")
    allowed = taxonomy()
    out = df.copy()
    coverage: dict[str, int] = {}

    for idx, row in out.iterrows():
        category, confidence, rule_id = categorize_row(row, rules, allowed)
        out.at[idx, "household_category"] = category
        out.at[idx, "spending_class"] = spending_class_for_retail_category(category)
        out.at[idx, "category_confidence"] = confidence
        out.at[idx, "category_rule_id"] = rule_id
        if category == "Unknown_Review":
            out.at[idx, "needs_review"] = True
            out.at[idx, "review_reason"] = append_reason(row.get("review_reason", ""), "unknown category")
        coverage[rule_id or "none"] = coverage.get(rule_id or "none", 0) + 1

    coverage_df = pd.DataFrame(
        [{"category_rule_id": rule_id, "matched_rows": count} for rule_id, count in sorted(coverage.items())]
    )
    return out, coverage_df


def categorize_row(row: pd.Series, rules: dict, allowed: set[str]) -> tuple[str, str, str]:
    for field in ("sku", "asin", "upc"):
        value = str(row.get(field, "") or "").strip()
        match = rules.get("exact_identifiers", {}).get(field, {}).get(value)
        if match:
            return _validated(match, allowed, "exact_identifier")

    desc = normalize_text(row.get("item_description_raw", ""))
    match = rules.get("exact_descriptions", {}).get(desc)
    if match:
        return _validated(match, allowed, "exact_description")

    for rule in rules.get("keyword_rules", []):
        if any(normalize_text(keyword) in desc for keyword in rule.get("keywords", [])):
            return _validated(rule, allowed, "keyword")

    retailer = normalize_text(row.get("retailer", ""))
    match = rules.get("retailer_fallbacks", {}).get(retailer)
    if match:
        return _validated(match, allowed, "retailer_fallback")

    return "Unknown_Review", "unknown", "unknown_review"


def _validated(rule: dict, allowed: set[str], confidence: str) -> tuple[str, str, str]:
    category = rule.get("category", "Unknown_Review")
    if category not in allowed:
        raise ValueError(f"Rule {rule.get('rule_id')} uses category outside taxonomy: {category}")
    return category, confidence, rule.get("rule_id", "")


def append_reason(existing: object, reason: str) -> str:
    current = "" if existing is None else str(existing).strip()
    if not current or current.lower() == "nan":
        return reason
    if reason in current:
        return current
    return f"{current}; {reason}"
