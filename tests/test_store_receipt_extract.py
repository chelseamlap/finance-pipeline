from decimal import Decimal

import pandas as pd

from finance_pipeline.loaders import store_receipt_extract
from finance_pipeline.source_dates import max_date_for_file


def test_store_receipt_extract_loads_paired_csv_exports(tmp_path):
    (tmp_path / "orders_target_20260524-120000.csv").write_text(
        "retailer,order_id,account_hint,ordered_at,total,subtotal,tax,shipping,fulfillment_type,item_count\n"
        "target,T-1,chelsea@example.com,2026-05-20T10:00:00Z,58.78,53.49,5.19,0,ShipToHome,2\n"
    )
    (tmp_path / "order_items_target_20260524-120000.csv").write_text(
        "retailer,order_id,line_index,sku,name,quantity,unit_price,line_total,category_native\n"
        "target,T-1,0,11111111,Unsalted Roasted Mixed Nuts,1,5.49,5.49,058\n"
        "target,T-1,1,22222222,Paper Towels,2,24.00,48.00,253\n"
    )

    df = store_receipt_extract.load(tmp_path, "batch")

    assert len(df) == 2
    assert set(df["retailer"]) == {"target"}
    assert df["source_adapter"].unique().tolist() == ["store_receipt_extract"]
    assert df["order_id"].unique().tolist() == ["T-1"]
    assert df["source_grand_total"].dropna().unique().tolist() == [Decimal("58.78")]
    assert df["source_tax_total"].dropna().unique().tolist() == [Decimal("5.19")]
    assert df["source_category_raw"].tolist() == ["058", "253"]
    assert df["allocated_total"].sum() == Decimal("58.68")


def test_store_receipt_extract_allocates_fully_unpriced_orders_for_review(tmp_path):
    (tmp_path / "orders_target.csv").write_text(
        "retailer,order_channel,order_id,account_hint,ordered_at,total,subtotal,tax,shipping,fulfillment_type,item_count\n"
        "target,online,T-UNPRICED,,2026-05-20T10:00:00Z,10.01,,,,ShipToHome,3\n"
    )
    (tmp_path / "order_items_target.csv").write_text(
        "retailer,order_channel,order_id,line_index,sku,name,quantity,unit_price,line_total,category_native,category_label,is_adjustment,adjustment_reason\n"
        "target,online,T-UNPRICED,0,SKU-1,Item One,1,,,,,false,\n"
        "target,online,T-UNPRICED,1,SKU-2,Item Two,1,,,,,false,\n"
        "target,online,T-UNPRICED,2,SKU-3,Item Three,1,,,,,false,\n"
    )

    df = store_receipt_extract.load(tmp_path, "batch")

    assert df["item_subtotal"].tolist() == [Decimal("3.34"), Decimal("3.34"), Decimal("3.33")]
    assert df["allocated_total"].sum() == Decimal("10.01")
    assert df["needs_review"].tolist() == [True, True, True]
    assert df["review_reason"].str.contains("missing_line_total_evenly_allocated_from_order_total").all()


def test_store_receipt_extract_fallback_description_for_blank_item_name(tmp_path):
    (tmp_path / "orders_target.csv").write_text(
        "retailer,order_channel,order_id,account_hint,ordered_at,total,subtotal,tax,shipping,fulfillment_type,item_count\n"
        "target,online,T-BLANK,,2026-05-20T10:00:00Z,16.00,16.00,0,0,ShipToHome,1\n"
    )
    (tmp_path / "order_items_target.csv").write_text(
        "retailer,order_channel,order_id,line_index,sku,name,quantity,unit_price,line_total,category_native,category_label,is_adjustment,adjustment_reason\n"
        "target,online,T-BLANK,0,90573590,,1,16,16,327,Dept 327,false,\n"
    )

    df = store_receipt_extract.load(tmp_path, "batch")
    row = df.iloc[0]

    assert row["item_description_raw"] == "Target item 90573590 (327: Dept 327)"
    assert row["source_category_raw"] == "327: Dept 327"
    assert bool(row["needs_review"]) is True
    assert "missing item name" in row["review_reason"]


def test_store_receipt_extract_loads_costco_csv_exports(tmp_path):
    (tmp_path / "orders_costco_20260524-120000.csv").write_text(
        "retailer,order_id,account_hint,ordered_at,total,subtotal,tax,shipping,fulfillment_type,item_count\n"
        "costco,C-1,,2026-04-02T10:15:00Z,142.37,130.00,12.37,0,,1\n"
    )
    (tmp_path / "order_items_costco_20260524-120000.csv").write_text(
        "retailer,order_id,line_index,sku,name,quantity,unit_price,line_total,category_native\n"
        "costco,C-1,0,1111111,Organic Eggs,2,9.99,19.98,\n"
    )

    df = store_receipt_extract.load(tmp_path, "batch")

    assert len(df) == 1
    row = df.iloc[0]
    assert row["retailer"] == "costco"
    assert row["sku"] == "1111111"
    assert row["quantity"] == Decimal("2.00")
    assert row["item_subtotal"] == Decimal("19.98")
    assert row["source_grand_total"] == Decimal("142.37")


def test_store_receipt_extract_keeps_latest_duplicate_csv_export(tmp_path):
    for stamp, total, name in [
        ("20260524-120000", "10.00", "Old Name"),
        ("20260524-130000", "12.00", "New Name"),
    ]:
        (tmp_path / f"orders_target_{stamp}.csv").write_text(
            "retailer,order_id,account_hint,ordered_at,total,subtotal,tax,shipping,fulfillment_type,item_count\n"
            f"target,T-1,,2026-05-20T10:00:00Z,{total},{total},0,0,ShipToHome,1\n"
        )
        (tmp_path / f"order_items_target_{stamp}.csv").write_text(
            "retailer,order_id,line_index,sku,name,quantity,unit_price,line_total,category_native\n"
            f"target,T-1,0,11111111,{name},1,{total},{total},058\n"
        )

    df = store_receipt_extract.load(tmp_path, "batch")

    assert len(df) == 1
    assert df.iloc[0]["item_description_raw"] == "New Name"
    assert df.iloc[0]["source_grand_total"] == Decimal("12.00")


def test_store_receipt_extract_loads_json_export(tmp_path):
    (tmp_path / "order_history_target_20260524-120000.json").write_text(
        """
        {
          "exported_at": "2026-05-24T12:00:00Z",
          "schema_version": 1,
          "orders": [
            {
              "retailer": "target",
              "order_id": "T-JSON",
              "account_hint": null,
              "ordered_at": "2026-05-20T10:00:00Z",
              "total": 5.49,
              "subtotal": 5.49,
              "tax": 0,
              "shipping": 0,
              "items": [
                {"line_index": 0, "sku": "11111111", "name": "Mixed Nuts", "quantity": 1, "unit_price": 5.49, "line_total": 5.49, "category_native": "058", "dpci": "058-02-1234"}
              ]
            }
          ]
        }
        """
    )

    df = store_receipt_extract.load(tmp_path, "batch")

    assert len(df) == 1
    assert df.iloc[0]["order_id"] == "T-JSON"
    assert df.iloc[0]["item_description_raw"] == "Mixed Nuts"
    assert df.iloc[0]["source_category_raw"] == "058"


def test_store_receipt_extract_dedupes_same_item_across_csv_and_json(tmp_path):
    (tmp_path / "orders_target.csv").write_text(
        "retailer,order_id,account_hint,ordered_at,total,subtotal,tax,shipping,fulfillment_type,item_count\n"
        "target,T-1,,2026-05-20T10:00:00Z,5.49,5.49,0,0,ShipToHome,1\n"
    )
    (tmp_path / "order_items_target.csv").write_text(
        "retailer,order_id,line_index,sku,name,quantity,unit_price,line_total,category_native\n"
        "target,T-1,0,11111111,Mixed Nuts,1,5.49,5.49,058\n"
    )
    (tmp_path / "order_history_target.json").write_text(
        """
        {
          "schema_version": 1,
          "orders": [
            {
              "retailer": "target",
              "order_id": "T-1",
              "ordered_at": "2026-05-20T10:00:00Z",
              "total": 5.49,
              "subtotal": 5.49,
              "tax": 0,
              "shipping": 0,
              "items": [
                {"line_index": 0, "sku": "11111111", "name": "Mixed Nuts", "quantity": 1, "unit_price": 5.49, "line_total": 5.49, "category_native": "058"}
              ]
            }
          ]
        }
        """
    )

    df = store_receipt_extract.load(tmp_path, "batch")

    assert len(df) == 1


def test_store_receipt_extract_store_filter(tmp_path):
    orders = pd.DataFrame(
        [
            {"retailer": "target", "order_id": "T-1", "account_hint": "", "ordered_at": "2026-05-20", "total": "1", "subtotal": "1", "tax": "0", "shipping": "0", "fulfillment_type": "", "item_count": "1"},
            {"retailer": "costco", "order_id": "C-1", "account_hint": "", "ordered_at": "2026-05-20", "total": "1", "subtotal": "1", "tax": "0", "shipping": "0", "fulfillment_type": "", "item_count": "1"},
        ]
    )
    items = pd.DataFrame(
        [
            {"retailer": "target", "order_id": "T-1", "line_index": "0", "sku": "T", "name": "Target Item", "quantity": "1", "unit_price": "1", "line_total": "1", "category_native": ""},
            {"retailer": "costco", "order_id": "C-1", "line_index": "0", "sku": "C", "name": "Costco Item", "quantity": "1", "unit_price": "1", "line_total": "1", "category_native": ""},
        ]
    )
    orders.to_csv(tmp_path / "orders_both.csv", index=False)
    items.to_csv(tmp_path / "order_items_both.csv", index=False)

    df = store_receipt_extract.load(tmp_path, "batch", store="target")

    assert df["retailer"].tolist() == ["target"]


def test_store_receipt_extract_source_date_reads_ordered_at(tmp_path):
    orders = tmp_path / "orders_target.csv"
    orders.write_text(
        "retailer,order_id,account_hint,ordered_at,total,subtotal,tax,shipping,fulfillment_type,item_count\n"
        "target,T-1,,2026-05-20T10:00:00Z,1,1,0,0,ShipToHome,1\n"
    )

    max_date, dated_rows, status = max_date_for_file("store_receipt_extract", orders)

    assert max_date is not None
    assert max_date.isoformat() == "2026-05-20"
    assert dated_rows == 1
    assert status == "ok"
