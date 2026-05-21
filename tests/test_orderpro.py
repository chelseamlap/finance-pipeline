from pathlib import Path
import shutil
from decimal import Decimal

from finance_pipeline.loaders import orderpro


def test_orderpro_multiple_stores(tmp_path):
    target = tmp_path / "target"
    amazon = tmp_path / "amazon"
    target.mkdir()
    amazon.mkdir()
    for name in ["orderpro_target_orders.csv", "orderpro_target_items.csv"]:
        shutil.copy(Path("tests/fixtures") / name, target / name)
    for name in ["orderpro_amazon_orders.csv", "orderpro_amazon_items.csv"]:
        shutil.copy(Path("tests/fixtures") / name, amazon / name)

    target_df = orderpro.load(target, "batch", "target")
    amazon_df = orderpro.load(amazon, "batch", "amazon")

    assert len(target_df) == 2
    assert len(amazon_df) == 1
    assert set(target_df["retailer"]) == {"target"}
    assert set(amazon_df["retailer"]) == {"amazon"}
    assert "source_category_raw" in target_df.columns
    assert target_df["allocated_total"].sum() == Decimal("18.50")


def test_orderpro_skips_single_value_summary_rows():
    from finance_pipeline.loaders.orderpro import _is_summary_or_blank_row

    assert _is_summary_or_blank_row({"Product Order Type": "205", "quantity": "222", "source_tab_name": "Purchased Items"})
    assert _is_summary_or_blank_row({"Product Order Type": "362", "quantity": "366", "Currency": "$", "source_tab_name": "Purchased Items"})
    assert _is_summary_or_blank_row({"Product Order Type": "Total Items", "quantity": "Total Quantity", "Currency": "Currency", "source_tab_name": "Purchased Items"})
    assert _is_summary_or_blank_row({"Product Order Type": "", "Order ID": "", "Product Description": ""})
    assert not _is_summary_or_blank_row({"Product Order Type": "Regular", "Order ID": "", "Product Description": ""})


def test_orderpro_skips_non_data_google_sheet_tabs():
    import pandas as pd
    from finance_pipeline.loaders.orderpro import _is_non_orderpro_tab

    assert _is_non_orderpro_tab(pd.DataFrame({"source_tab_name": ["Pivot Table 5"], "Product Type": ["GROCERY"]}))
    assert not _is_non_orderpro_tab(pd.DataFrame({"source_tab_name": ["Purchased Items"], "Product Type": ["GROCERY"]}))
    assert not _is_non_orderpro_tab(pd.DataFrame({"source_tab_name": ["Order History"], "Order ID": ["T-1"]}))


def test_orderpro_fallback_order_id_is_deterministic():
    from finance_pipeline.loaders.orderpro import _fallback_order_id

    row = {
        "transaction_date": "Sep-07-2024",
        "sku": "497900518368",
        "item_description_raw": "Promotional Email GiftCard ",
        "item_subtotal": "5",
        "Tracking Number": "042600637376028",
    }

    first = _fallback_order_id("target", row)
    second = _fallback_order_id("target", row)

    assert first.startswith("missing-order:")
    assert first == second


def test_orderpro_fallback_description_uses_sku_and_category():
    from finance_pipeline.loaders.orderpro import _fallback_description

    desc = _fallback_description({"sku": "92540092", "source_category_raw": "Store Purchase"})

    assert desc == "Target item 92540092 (Store Purchase)"
