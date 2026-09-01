from pathlib import Path

from finance_pipeline.loaders import simplifi


def test_simplifi_aliases():
    df = simplifi.load(Path("tests/fixtures/simplifi.csv"), "batch")
    assert len(df) == 3
    assert set(["posted_date", "merchant_normalized", "amount", "simplifi_category_mapped", "spending_class"]).issubset(df.columns)
    assert df.loc[0, "merchant_normalized"] == "amazon"


def test_simplifi_category_migration_and_spending_class():
    df = simplifi.load(Path("tests/fixtures/simplifi.csv"), "batch")
    assert df.loc[2, "simplifi_category_mapped"] == "02 Groceries and Foods"
    assert df.loc[2, "spending_class"] == "Variable Required"


def test_simplifi_blended_category_passes_through_when_unmapped():
    df = simplifi.load(Path("tests/fixtures/simplifi.csv"), "batch")
    assert df.loc[0, "blended_category"] == "Shopping"


def test_simplifi_blended_category_renames_through_the_mapped_category():
    df = simplifi.load(Path("tests/fixtures/simplifi.csv"), "batch")
    # raw category "Groceries" -> simplifi_category_mapped "02 Groceries and Foods"
    # (config/simplifi_category_mapping.yaml) -> blended_category "Groceries".
    assert df.loc[2, "blended_category"] == "Groceries"
