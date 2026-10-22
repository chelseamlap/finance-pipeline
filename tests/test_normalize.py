from finance_pipeline.normalize import blended_category_for_simplifi, map_simplifi_category, spending_class_for_category


def test_blended_category_renames_overlapping_categories():
    assert blended_category_for_simplifi("02 Groceries and foods") == "Groceries"
    assert blended_category_for_simplifi("03 Alcohol and bars") == "Alcohol"
    assert blended_category_for_simplifi("03 Not home cooked foods:Restaurants") == "Restaurants_Takeout"


def test_blended_category_folds_costco_into_household():
    assert blended_category_for_simplifi("02 Household Operations") == "Household"


def test_blended_category_folds_renamed_retail_alcohol():
    # "Alcohol and bars" was renamed to "Retail Alcohol" in Simplifi (2026-09) to
    # split store-bought alcohol from bar/restaurant tabs; both spellings must
    # resolve to the same canonical "Alcohol" so historical and future rows blend.
    assert blended_category_for_simplifi("03 Retail Alcohol") == "Alcohol"
    assert blended_category_for_simplifi("03 Alcohol and bars") == "Alcohol"


def test_blended_category_bars_under_not_home_cooked_still_routes_to_restaurants():
    # Bar/restaurant tabs move under "Not home cooked foods" subcategories -- the
    # rename is top-level-only, so any subcategory under it still resolves correctly.
    assert blended_category_for_simplifi("03 Not home cooked foods:Bars") == "Restaurants_Takeout"


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


def test_sinking_categories_keep_their_own_label_not_the_spending_class_name():
    # simplifi_category_mapping.yaml used to map these to "05 Sinking:<label>" --
    # blended_category_for_simplifi takes everything before the first colon, so
    # the real label ("Auto Registration", "Education", "Tuition") was silently
    # discarded and every one of these showed up as a bare "Sinking" category
    # instead of its own name. spending_class must still resolve to Sinking via
    # the "05" prefix either way.
    for raw, expected_label in [
        ("Auto & Transport:Registration", "Auto Registration"),
        ("Auto & Transport:Service & Parts", "Auto Service & Parts"),
        ("Ski Passes", "Ski Passes"),
        ("Education", "Education"),
        ("Education:Tuition", "Tuition"),
    ]:
        mapped = map_simplifi_category(raw)
        assert blended_category_for_simplifi(mapped) == expected_label
        assert spending_class_for_category(mapped) == "Sinking"
