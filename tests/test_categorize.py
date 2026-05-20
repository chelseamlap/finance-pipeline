from pathlib import Path

from finance_pipeline.categorize import categorize_items
from finance_pipeline.loaders import amazon_order_history_reporter


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
