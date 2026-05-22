from pathlib import Path

from finance_pipeline.categorize import categorize_items
from finance_pipeline.loaders import amazon_order_history_reporter
from finance_pipeline.storage import MemoryStateStore


def test_category_rules_are_deterministic_and_unknown_reviews():
    df = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    categorized_a, coverage_a = categorize_items(df)
    categorized_b, coverage_b = categorize_items(df)
    assert categorized_a["household_category"].tolist() == categorized_b["household_category"].tolist()
    assert categorized_a.loc[0, "household_category"] == "Groceries"
    assert categorized_a.loc[0, "spending_class"] == "Variable Required"

    df.loc[0, ["asin", "sku", "upc", "item_description_raw", "item_description_normalized"]] = ["", "", "", "Mystery Object", "mystery object"]
    unknown, _ = categorize_items(df.iloc[[0]])
    assert unknown.loc[0, "household_category"] == "Unknown_Review"
    assert bool(unknown.loc[0, "needs_review"])


def test_search_override_beats_broad_keyword_rule():
    df = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    df.loc[0, ["asin", "sku", "upc", "item_description_raw", "item_description_normalized"]] = [
        "",
        "",
        "",
        "La Roche Posay Lipikar Skin Milk Lotion",
        "la roche posay lipikar skin milk lotion",
    ]

    categorized, _ = categorize_items(df.iloc[[0]])

    assert categorized.loc[0, "household_category"] == "Health_Personal_Care"
    assert categorized.loc[0, "category_rule_id"] == "override:la_roche_posay_skin_milk"


def test_broad_keyword_rule_still_matches_general_milk():
    df = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    df.loc[0, ["asin", "sku", "upc", "item_description_raw", "item_description_normalized"]] = [
        "",
        "",
        "",
        "Whole Milk One Gallon",
        "whole milk one gallon",
    ]

    categorized, _ = categorize_items(df.iloc[[0]])

    assert categorized.loc[0, "household_category"] == "Groceries"
    assert categorized.loc[0, "category_rule_id"] == "kw:grocery"


def test_categorization_saves_historical_item_mapping_for_resolved_category():
    df = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    store = MemoryStateStore()

    categorized, _ = categorize_items(df.iloc[[0]], mapping_store=store)

    key = ("asin", f"amazon:{df.loc[0, 'asin']}")
    assert categorized.loc[0, "household_category"] == "Groceries"
    assert store.category_mappings[key]["category"] == "Groceries"
    assert store.category_mappings[key]["source"] == "exact_identifier"
    assert store.category_mappings[key]["reviewed"] is False
    assert store.category_mappings[key]["original_item_description"] == df.loc[0, "item_description_raw"]
    assert store.category_mappings[key]["normalized_item_description"] == df.loc[0, "item_description_normalized"]
    assert store.category_mappings[key]["retailer"] == "amazon"
    assert store.category_mappings[key]["source_adapter"] == "amazon_order_history_reporter"
    assert store.category_mappings[key]["item_id"] == df.loc[0, "item_id"]
    assert store.category_mappings[key]["file_source"] == str(Path("tests/fixtures/amazon_ohr.csv"))
    assert store.category_mappings[key]["import_batch_id"] == "batch"
    assert store.category_mappings[key]["created_from_rule_id"] == f"asin:{df.loc[0, 'asin']}"


def test_historical_mapping_is_reused_before_rules_change_category():
    df = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    store = MemoryStateStore()
    store.upsert_mapping("asin", f"amazon:{df.loc[0, 'asin']}", "Health_Personal_Care", source="historical_rule", reviewed=False)

    categorized, _ = categorize_items(df.iloc[[0]], mapping_store=store)

    assert categorized.loc[0, "household_category"] == "Health_Personal_Care"
    assert categorized.loc[0, "category_rule_id"] == f"saved:asin:amazon:{df.loc[0, 'asin']}"


def test_unknown_category_is_not_saved_as_historical_mapping():
    df = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    df.loc[0, ["asin", "sku", "upc", "item_description_raw", "item_description_normalized", "merchant_raw", "merchant_normalized"]] = [
        "",
        "",
        "",
        "Mystery Object",
        "mystery object",
        "",
        "",
    ]
    store = MemoryStateStore()

    categorized, _ = categorize_items(df.iloc[[0]], mapping_store=store)

    assert categorized.loc[0, "household_category"] == "Unknown_Review"
    assert store.category_mappings == {}
    assert len(store.mapping_candidates) == 1
    candidate = next(iter(store.mapping_candidates.values()))
    assert candidate["reason"] == "unknown_category"
    assert candidate["mapping_type"] == "description"
    assert candidate["mapping_key"] == "amazon:mystery object"
    assert candidate["original_item_description"] == "Mystery Object"


def test_conflicting_historical_mapping_is_queued_for_review():
    df = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    store = MemoryStateStore()
    key = f"amazon:{df.loc[0, 'asin']}"
    store.upsert_mapping("asin", key, "Health_Personal_Care", source="manual")

    categorize_items(df.iloc[[0]], mapping_store=store)

    assert store.category_mappings[("asin", key)]["category"] == "Health_Personal_Care"
    assert len(store.mapping_candidates) == 1
    candidate = next(iter(store.mapping_candidates.values()))
    assert candidate["reason"] == "mapping_conflict"
    assert candidate["suggested_category"] == "Groceries"
    assert candidate["evidence"] == "Saved mapping category is Health_Personal_Care"
