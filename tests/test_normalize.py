from finance_pipeline.normalize import blended_category_for_simplifi


def test_blended_category_renames_overlapping_categories():
    assert blended_category_for_simplifi("02 Groceries and foods") == "Groceries"
    assert blended_category_for_simplifi("03 Alcohol and bars") == "Alcohol"
    assert blended_category_for_simplifi("03 Not home cooked foods:Restaurants") == "Restaurants_Takeout"


def test_blended_category_folds_costco_into_household():
    assert blended_category_for_simplifi("02 Household Operations") == "Household"


def test_blended_category_passes_through_unmapped_categories():
    assert blended_category_for_simplifi("Mortgage and Escrow") == "Mortgage and Escrow"
    assert blended_category_for_simplifi("Utilities") == "Utilities"


def test_blended_category_handles_blank_input():
    assert blended_category_for_simplifi("") == ""
    assert blended_category_for_simplifi(None) == ""
