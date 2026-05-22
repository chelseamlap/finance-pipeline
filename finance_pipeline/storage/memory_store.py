from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pandas as pd


class MemoryStateStore:
    def __init__(self) -> None:
        self.transactions: dict[str, dict] = {}
        self.retail_items: dict[str, dict] = {}
        self.category_mappings: dict[tuple[str, str], dict] = {}
        self.mapping_candidates: dict[str, dict] = {}

    def close(self) -> None:
        return None

    def upsert_transactions(self, df: pd.DataFrame, run_id: str) -> int:
        return self._upsert(self.transactions, "transaction_id", df, run_id)

    def upsert_retail_items(self, df: pd.DataFrame, run_id: str) -> int:
        return self._upsert(self.retail_items, "item_id", df, run_id)

    def upsert_mapping(
        self,
        mapping_type: str,
        mapping_key: str,
        category: str,
        source: str,
        confidence: str = "manual",
        reviewed: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "mapping_type": mapping_type,
            "mapping_key": mapping_key,
            "category": category,
            "source": source,
            "confidence": confidence,
            "reviewed": reviewed,
        }
        if metadata:
            record.update(json.loads(json.dumps(metadata, default=_json_default)))
        self.category_mappings[(mapping_type, mapping_key)] = record

    def get_mapping(self, mapping_type: str, mapping_key: str) -> dict | None:
        return self.category_mappings.get((mapping_type, mapping_key))

    def list_mappings(self) -> list[dict]:
        return sorted(self.category_mappings.values(), key=lambda row: (row.get("mapping_type", ""), row.get("mapping_key", "")))

    def upsert_mapping_candidate(self, candidate: dict[str, Any]) -> None:
        record = json.loads(json.dumps(candidate, default=_json_default))
        self.mapping_candidates[str(record["candidate_id"])] = record

    def list_mapping_candidates(self) -> list[dict]:
        return sorted(self.mapping_candidates.values(), key=lambda row: (row.get("status", ""), row.get("candidate_id", "")))

    def _upsert(self, records: dict[str, dict], id_column: str, df: pd.DataFrame, run_id: str) -> int:
        if df.empty:
            return 0
        for row in df.to_dict("records"):
            record_id = str(row[id_column])
            current = records.get(record_id, {})
            records[record_id] = {
                "record_id": record_id,
                "row_fingerprint": str(row.get("row_fingerprint") or ""),
                "payload": json.loads(json.dumps(row, default=_json_default)),
                "first_seen_run_id": current.get("first_seen_run_id", run_id),
                "last_seen_run_id": run_id,
                "is_active": True,
            }
        return len(df)


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)
