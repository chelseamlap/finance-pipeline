from __future__ import annotations

import pandas as pd

from .identity import item_mapping_keys_for_retail_item, mapping_keys_for_retail_item, stable_hash
from .normalize import clean_string, normalize_text, spending_class_for_retail_category


class CachedMappingStore:
    def __init__(self, mapping_store) -> None:
        self.mapping_store = mapping_store
        self.mapping_cache: dict[tuple[str, str], dict | None] = {}
        self.candidate_ids: set[str] = set()
        for mapping in getattr(mapping_store, "list_mappings", lambda: [])():
            key = (str(mapping.get("mapping_type", "")), str(mapping.get("mapping_key", "")))
            if all(key):
                self.mapping_cache[key] = mapping

    def get_mapping(self, mapping_type: str, mapping_key: str) -> dict | None:
        key = (mapping_type, mapping_key)
        if key not in self.mapping_cache:
            self.mapping_cache[key] = self.mapping_store.get_mapping(mapping_type, mapping_key)
        return self.mapping_cache[key]

    def upsert_mapping(self, mapping_type: str, mapping_key: str, category: str, source: str, **kwargs) -> None:
        self.mapping_store.upsert_mapping(mapping_type, mapping_key, category, source, **kwargs)
        record = {
            "mapping_type": mapping_type,
            "mapping_key": mapping_key,
            "category": category,
            "source": source,
            "confidence": kwargs.get("confidence", "manual"),
            "reviewed": kwargs.get("reviewed", True),
        }
        metadata = kwargs.get("metadata")
        if metadata:
            record.update(metadata)
        self.mapping_cache[(mapping_type, mapping_key)] = record

    def upsert_mapping_candidate(self, candidate: dict) -> None:
        candidate_id = str(candidate["candidate_id"])
        if candidate_id in self.candidate_ids:
            return
        self.mapping_store.upsert_mapping_candidate(candidate)
        self.candidate_ids.add(candidate_id)

    def __getattr__(self, name: str):
        return getattr(self.mapping_store, name)


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


def save_historical_item_mapping(row: dict, category: str, mapping_store, source: str = "historical_rule") -> dict | None:
    keys = item_mapping_keys_for_retail_item(row)
    if not keys:
        return None
    mapping_type, mapping_key = keys[0]
    existing = mapping_store.get_mapping(mapping_type, mapping_key)
    if existing:
        if existing.get("category") != category:
            enqueue_mapping_candidate(
                row,
                mapping_store,
                reason="mapping_conflict",
                suggested_category=category,
                source=source,
                confidence=source,
                evidence=f"Existing mapping category is {existing.get('category', '')}",
            )
        return existing
    mapping_store.upsert_mapping(
        mapping_type,
        mapping_key,
        category,
        source=source,
        confidence=source,
        reviewed=False,
        metadata=_historical_mapping_metadata(row),
    )
    return {
        "mapping_type": mapping_type,
        "mapping_key": mapping_key,
        "category": category,
        "source": source,
        "confidence": source,
        "reviewed": False,
        **_historical_mapping_metadata(row),
    }


def enqueue_mapping_candidate(
    row: dict,
    mapping_store,
    reason: str,
    suggested_category: str = "",
    source: str = "review_queue",
    confidence: str = "needs_review",
    evidence: str = "",
) -> dict | None:
    keys = item_mapping_keys_for_retail_item(row)
    if not keys:
        return None
    mapping_type, mapping_key = keys[0]
    candidate = {
        "candidate_id": stable_hash([mapping_type, mapping_key, reason], length=40),
        "mapping_type": mapping_type,
        "mapping_key": mapping_key,
        "suggested_category": suggested_category,
        "reason": reason,
        "source": source,
        "confidence": confidence,
        "status": "needs_review",
        "evidence": evidence,
        **_historical_mapping_metadata(row),
    }
    mapping_store.upsert_mapping_candidate(candidate)
    return candidate


def export_mapping_tables(mapping_store) -> tuple[pd.DataFrame, pd.DataFrame]:
    mappings = pd.DataFrame(mapping_store.list_mappings())
    candidates = pd.DataFrame(mapping_store.list_mapping_candidates())
    return mappings, candidates


def _historical_mapping_metadata(row: dict) -> dict[str, object]:
    original_description = clean_string(row.get("item_description_raw"))
    normalized_description = normalize_text(original_description or row.get("item_description_normalized"))
    metadata = {
        "original_item_description": original_description,
        "normalized_item_description": normalized_description,
        "retailer": clean_string(row.get("retailer")),
        "source_adapter": clean_string(row.get("source_adapter")),
        "source_owner": clean_string(row.get("source_owner")),
        "item_id": clean_string(row.get("item_id")),
        "order_id": clean_string(row.get("order_id")),
        "receipt_id": clean_string(row.get("receipt_id")),
        "file_source": clean_string(row.get("file_source")),
        "import_batch_id": clean_string(row.get("import_batch_id")),
        "created_from_rule_id": clean_string(row.get("category_rule_id")),
    }
    return {key: value for key, value in metadata.items() if value != ""}
