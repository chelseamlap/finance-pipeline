from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from finance_pipeline.identity import stable_hash


BATCH_LIMIT = 450


class FirestoreStateStore:
    def __init__(self, project: str | None = None, collection_prefix: str = "finance_pipeline") -> None:
        try:
            from google.cloud import firestore
        except ImportError as exc:
            raise RuntimeError("Install google-cloud-firestore to use FirestoreStateStore.") from exc

        self.firestore = firestore
        self.client = firestore.Client(project=project)
        self.collection_prefix = collection_prefix

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()

    def upsert_transactions(self, df: pd.DataFrame, run_id: str) -> int:
        return self._upsert_record_state(f"{self.collection_prefix}_transaction_state", "transaction_id", df, run_id)

    def upsert_retail_items(self, df: pd.DataFrame, run_id: str) -> int:
        return self._upsert_record_state(f"{self.collection_prefix}_retail_item_state", "item_id", df, run_id)

    def upsert_mapping(
        self,
        mapping_type: str,
        mapping_key: str,
        category: str,
        source: str,
        confidence: str = "manual",
        reviewed: bool = True,
    ) -> None:
        doc_id = stable_hash([mapping_type, mapping_key], length=40)
        self.client.collection(f"{self.collection_prefix}_category_mappings").document(doc_id).set(
            {
                "mapping_type": mapping_type,
                "mapping_key": mapping_key,
                "category": category,
                "source": source,
                "confidence": confidence,
                "reviewed": reviewed,
                "updated_at": _now(),
            },
            merge=True,
        )

    def get_mapping(self, mapping_type: str, mapping_key: str) -> dict | None:
        doc_id = stable_hash([mapping_type, mapping_key], length=40)
        snapshot = self.client.collection(f"{self.collection_prefix}_category_mappings").document(doc_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    def _upsert_record_state(self, collection_name: str, id_column: str, df: pd.DataFrame, run_id: str) -> int:
        if df.empty:
            return 0
        collection = self.client.collection(collection_name)
        count = 0
        batch = self.client.batch()
        for record in df.to_dict("records"):
            record_id = str(record[id_column])
            ref = collection.document(record_id)
            snapshot = ref.get()
            first_seen = snapshot.to_dict().get("first_seen_run_id") if snapshot.exists else run_id
            batch.set(
                ref,
                {
                    "record_id": record_id,
                    "row_fingerprint": str(record.get("row_fingerprint") or ""),
                    "first_seen_run_id": first_seen,
                    "last_seen_run_id": run_id,
                    "is_active": True,
                    "updated_at": _now(),
                },
                merge=True,
            )
            count += 1
            if count % BATCH_LIMIT == 0:
                batch.commit()
                batch = self.client.batch()
        batch.commit()
        return count


def _now() -> str:
    return datetime.now(UTC).isoformat()
