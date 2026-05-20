from __future__ import annotations

import pandas as pd

from .identity import mapping_keys_for_retail_item
from .normalize import spending_class_for_retail_category


def apply_saved_mappings(df: pd.DataFrame, mapping_store) -> pd.DataFrame:
    if df.empty or mapping_store is None:
        return df
    out = df.copy()
    for idx, row in out.iterrows():
        mapping = first_saved_mapping(row.to_dict(), mapping_store)
        if not mapping:
            continue
        category = mapping["category"]
        out.at[idx, "household_category"] = category
        out.at[idx, "spending_class"] = spending_class_for_retail_category(category)
        out.at[idx, "category_confidence"] = mapping.get("confidence", "saved_mapping")
        out.at[idx, "category_rule_id"] = f"saved:{mapping['mapping_type']}:{mapping['mapping_key']}"
        out.at[idx, "needs_review"] = False
        out.at[idx, "review_reason"] = ""
    return out


def first_saved_mapping(row: dict, mapping_store) -> dict | None:
    for mapping_type, mapping_key in mapping_keys_for_retail_item(row):
        mapping = mapping_store.get_mapping(mapping_type, mapping_key)
        if mapping:
            return mapping
    return None


def save_mapping_for_retail_item(row: dict, category: str, mapping_store, source: str = "manual") -> None:
    keys = mapping_keys_for_retail_item(row)
    if not keys:
        raise ValueError("Cannot save mapping for row without an identifier, description, or merchant.")
    mapping_type, mapping_key = keys[0]
    mapping_store.upsert_mapping(mapping_type, mapping_key, category, source=source, confidence=source, reviewed=True)
