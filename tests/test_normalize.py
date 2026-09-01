from finance_pipeline.normalize import blended_category_for_simplifi


def test_blended_category_renames_overlapping_categories():
    assert blended_category_for_simplifi("02 Groceries and foods") == "Groceries"
    assert blended_category_for_simplifi("03 Alcohol and bars") == "Alcohol"
    assert blended_category_for_simplifi("03 Not home cooked foods:Restaurants") == "Restaurants_Takeout"


def test_blended_category_folds_costco_into_household():
    assert blended_category_for_simplifi("02 Household Operations") == "Household"


def test_blended_category_rename_is_case_insensitive():
    # simplifi_category_mapping.yaml maps raw "Groceries" -> "02 Groceries and Foods"
    # (capital F); real Simplifi exports use "02 Groceries and foods" (lowercase f).
    # Both must resolve to the same canonical "Groceries".
    assert blended_category_for_simplifi("02 Groceries and Foods") == "Groceries"
    assert blended_category_for_simplifi("02 Groceries and foods") == "Groceries"


def test_blended_category_passes_through_unmapped_categories():
    assert blended_category_for_simplifi("Mortgage and Escrow") == "Mortgage and Escrow"
    assert blended_category_for_simplifi("Utilities") == "Utilities"


def test_blended_category_handles_blank_input():
    assert blended_category_for_simplifi("") == ""
    assert blended_category_for_simplifi(None) == ""
