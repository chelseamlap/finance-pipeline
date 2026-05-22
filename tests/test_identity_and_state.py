from pathlib import Path
import shutil

from finance_pipeline.categorize import categorize_items
from finance_pipeline.loaders import orderpro
from finance_pipeline.storage.firestore_store import FirestoreStateStore
from finance_pipeline.storage import MemoryStateStore


def test_orderpro_item_ids_are_stable_when_file_order_changes(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for name in ["orderpro_target_orders.csv", "orderpro_target_items.csv"]:
        shutil.copy(Path("tests/fixtures") / name, first / name)
    for name in ["orderpro_target_items.csv", "orderpro_target_orders.csv"]:
        shutil.copy(Path("tests/fixtures") / name, second / name)

    first_df = orderpro.load(first, "batch-a", "target")
    second_df = orderpro.load(second, "batch-b", "target")

    assert sorted(first_df["item_id"]) == sorted(second_df["item_id"])
    assert first_df["row_fingerprint"].notna().all()


def test_saved_mapping_overrides_unknown_category(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ["orderpro_target_orders.csv", "orderpro_target_items.csv"]:
        shutil.copy(Path("tests/fixtures") / name, source / name)
    df = orderpro.load(source, "batch", "target")
    df.loc[0, ["sku", "asin", "upc", "item_description_raw", "item_description_normalized"]] = [
        "",
        "",
        "",
        "Whole Milk",
        "whole milk",
    ]

    store = MemoryStateStore()
    store.upsert_mapping("description", "target:whole milk", "Groceries", source="manual")

    categorized, _ = categorize_items(df.iloc[[0]], mapping_store=store)

    assert categorized.loc[categorized.index[0], "household_category"] == "Groceries"
    assert categorized.loc[categorized.index[0], "category_rule_id"] == "saved:description:target:whole milk"


def test_state_store_upsert_preserves_record_identity(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ["orderpro_target_orders.csv", "orderpro_target_items.csv"]:
        shutil.copy(Path("tests/fixtures") / name, source / name)
    df = orderpro.load(source, "batch", "target")

    store = MemoryStateStore()
    assert store.upsert_retail_items(df, "run-1") == len(df)
    assert store.upsert_retail_items(df, "run-2") == len(df)

    assert len(store.retail_items) == len(df)
    assert {row["last_seen_run_id"] for row in store.retail_items.values()} == {"run-2"}


def test_firestore_mapping_upsert_includes_audit_metadata():
    store = object.__new__(FirestoreStateStore)
    store.client = FakeFirestoreClient()
    store.collection_prefix = "test"

    store.upsert_mapping(
        "description",
        "target:whole milk",
        "Groceries",
        source="keyword",
        confidence="keyword",
        reviewed=False,
        metadata={
            "original_item_description": "Whole Milk",
            "normalized_item_description": "whole milk",
            "item_id": "item-1",
        },
    )

    payload = store.client.collections["test_category_mappings"].last_payload
    assert payload["mapping_type"] == "description"
    assert payload["mapping_key"] == "target:whole milk"
    assert payload["original_item_description"] == "Whole Milk"
    assert payload["normalized_item_description"] == "whole milk"
    assert payload["item_id"] == "item-1"
    assert "updated_at" in payload


class FakeFirestoreClient:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        collection = FakeFirestoreCollection()
        self.collections[name] = collection
        return collection


class FakeFirestoreCollection:
    def __init__(self):
        self.last_payload = None

    def document(self, doc_id):
        self.doc_id = doc_id
        return self

    def set(self, payload, merge=False):
        self.last_payload = payload
        self.merge = merge
