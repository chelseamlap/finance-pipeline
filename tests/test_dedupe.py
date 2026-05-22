from __future__ import annotations

from decimal import Decimal

import pandas as pd

from finance_pipeline.dedupe import dedupe_retail_items


def test_dedupe_retail_items_prefers_amazon_reporter_over_orderpro():
    df = pd.DataFrame(
        [
            {"item_id": "reporter-1", "retailer": "amazon", "order_id": "A-1", "source_adapter": "amazon_order_history_reporter", "allocated_total": 10},
            {"item_id": "reporter-2", "retailer": "amazon", "order_id": "A-1", "source_adapter": "amazon_order_history_reporter", "allocated_total": 5},
            {"item_id": "orderpro-1", "retailer": "amazon", "order_id": "A-1", "source_adapter": "orderpro", "allocated_total": 99},
            {"item_id": "target-1", "retailer": "target", "order_id": "T-1", "source_adapter": "orderpro", "allocated_total": 20},
        ]
    )

    out = dedupe_retail_items(df)

    assert out["item_id"].tolist() == ["reporter-1", "reporter-2", "target-1"]
    amazon = out[out["retailer"] == "amazon"]
    assert set(amazon["source_adapter"]) == {"amazon_order_history_reporter"}
    assert amazon["dedupe_notes"].str.contains("dropped orderpro").all()


def test_dedupe_retail_items_keeps_distinct_orderpro_order_when_no_preferred_duplicate():
    df = pd.DataFrame(
        [
            {"item_id": "orderpro-1", "retailer": "amazon", "order_id": "A-2", "source_adapter": "orderpro", "allocated_total": 12},
            {"item_id": "reporter-1", "retailer": "amazon", "order_id": "A-3", "source_adapter": "amazon_order_history_reporter", "allocated_total": 15},
        ]
    )

    out = dedupe_retail_items(df)

    assert out["item_id"].tolist() == ["orderpro-1", "reporter-1"]
    assert out["dedupe_notes"].fillna("").eq("").all()


def test_dedupe_retail_items_collapses_same_order_duplicate_item_rows():
    df = pd.DataFrame(
        [
            {
                "item_id": "costco-1",
                "retailer": "costco",
                "order_id": "C-1",
                "source_adapter": "orderpro",
                "transaction_date": "2026-01-20",
                "item_description_normalized": "street tacos",
                "quantity": 1,
                "unit_price": 5.49,
                "item_subtotal": 5.49,
                "item_discount": 0,
                "allocated_total": 5.49,
                "source_grand_total": 39.19,
                "file_source": "chelsea.gsheet",
            },
            {
                "item_id": "costco-2",
                "retailer": "costco",
                "order_id": "C-1",
                "source_adapter": "orderpro",
                "transaction_date": "2026-01-20",
                "item_description_normalized": "street tacos",
                "quantity": 1,
                "unit_price": 5.49,
                "item_subtotal": 5.49,
                "item_discount": 0,
                "allocated_total": 5.49,
                "source_grand_total": 39.19,
                "file_source": "michael.gsheet",
            },
            {
                "item_id": "costco-3",
                "retailer": "costco",
                "order_id": "C-1",
                "source_adapter": "orderpro",
                "transaction_date": "2026-01-20",
                "item_description_normalized": "oikos zero",
                "quantity": 1,
                "unit_price": 13.99,
                "item_subtotal": 13.99,
                "item_discount": 0,
                "allocated_total": 13.99,
                "source_grand_total": 39.19,
                "file_source": "chelsea.gsheet",
            },
        ]
    )

    out = dedupe_retail_items(df)

    assert out["item_id"].tolist() == ["costco-1", "costco-3"]
    assert out["allocated_total"].sum() == 19.48
    assert "collapsed 1 duplicate row" in out.loc[out["item_id"] == "costco-1", "dedupe_notes"].iloc[0]


def test_dedupe_retail_items_keeps_same_item_when_quantity_differs():
    df = pd.DataFrame(
        [
            {"item_id": "one", "retailer": "costco", "order_id": "C-2", "source_adapter": "orderpro", "item_description_normalized": "eggs", "quantity": 1, "unit_price": 9.39, "item_subtotal": 9.39, "allocated_total": 9.39},
            {"item_id": "two", "retailer": "costco", "order_id": "C-2", "source_adapter": "orderpro", "item_description_normalized": "eggs", "quantity": 2, "unit_price": 9.39, "item_subtotal": 18.78, "allocated_total": 18.78},
        ]
    )

    out = dedupe_retail_items(df)

    assert out["item_id"].tolist() == ["one", "two"]
    assert out["dedupe_notes"].fillna("").eq("").all()


def test_dedupe_retail_items_keeps_duplicate_count_that_matches_source_order_total():
    rows = []
    for i in range(4):
        rows.append(
            {
                "item_id": f"protein-{i}",
                "retailer": "costco",
                "order_id": "C-3",
                "source_adapter": "orderpro",
                "transaction_date": "2026-01-20",
                "item_description_normalized": "protein bars",
                "quantity": 1,
                "unit_price": 10.00,
                "item_subtotal": 10.00,
                "allocated_total": 10.00,
                "source_order_total": 25.00,
                "source_grand_total": 25.00,
            }
        )
    rows.append(
        {
            "item_id": "eggs",
            "retailer": "costco",
            "order_id": "C-3",
            "source_adapter": "orderpro",
            "transaction_date": "2026-01-20",
            "item_description_normalized": "eggs",
            "quantity": 1,
            "unit_price": 5.00,
            "item_subtotal": 5.00,
            "allocated_total": 5.00,
            "source_order_total": 25.00,
            "source_grand_total": 25.00,
        }
    )
    df = pd.DataFrame(rows)

    out = dedupe_retail_items(df)

    assert out["item_id"].tolist() == ["protein-0", "protein-1", "eggs"]
    assert out["item_subtotal"].sum() == 25.00
    note = out.loc[out["item_id"] == "protein-0", "dedupe_notes"].iloc[0]
    assert "kept 2 of 4" in note
    assert "source_order_total" in note


def test_dedupe_retail_items_sets_single_placeholder_item_to_source_order_total():
    df = pd.DataFrame(
        [
            {
                "item_id": "vet-1",
                "retailer": "costco",
                "order_id": "C-4",
                "source_adapter": "orderpro",
                "transaction_date": "2026-01-24",
                "item_description_normalized": "vet rx",
                "quantity": 2,
                "unit_price": 1.00,
                "item_subtotal_raw": 1.00,
                "line_subtotal_derived": 2.00,
                "item_subtotal": 1.00,
                "item_subtotal_derivation_notes": "item_subtotal_derived_from_quantity_times_unit_price",
                "allocated_total": 1.00,
                "source_order_total": 21.78,
                "source_grand_total": 21.78,
            }
        ]
    )

    out = dedupe_retail_items(df)

    assert out["item_id"].tolist() == ["vet-1"]
    assert out.loc[0, "item_subtotal"] == Decimal("21.78")
    assert out.loc[0, "line_subtotal_derived"] == Decimal("21.78")
    assert out.loc[0, "allocated_total"] == Decimal("21.78")
    assert out.loc[0, "item_subtotal_derivation_notes"] == "item_subtotal_derived_from_source_order_total"
    assert "item_subtotal set to source_order_total" in out.loc[0, "dedupe_notes"]


def test_dedupe_retail_items_sets_single_return_placeholder_to_negative_source_order_total():
    df = pd.DataFrame(
        [
            {
                "item_id": "return-1",
                "retailer": "costco",
                "order_id": "C-5",
                "source_adapter": "orderpro",
                "transaction_date": "2026-01-20",
                "item_description_normalized": "1899652",
                "quantity": 1,
                "unit_price": 5.00,
                "item_subtotal": 5.00,
                "allocated_total": 5.00,
                "source_order_total": -19.96,
                "source_tax_total": -1.82,
                "source_discount_total": 5.00,
                "source_grand_total": -21.78,
            },
            {
                "item_id": "return-2",
                "retailer": "costco",
                "order_id": "C-5",
                "source_adapter": "orderpro",
                "transaction_date": "2026-01-20",
                "item_description_normalized": "1899652",
                "quantity": 1,
                "unit_price": 5.00,
                "item_subtotal": 5.00,
                "allocated_total": 5.00,
                "source_order_total": -19.96,
                "source_tax_total": -1.82,
                "source_discount_total": 5.00,
                "source_grand_total": -21.78,
            },
        ]
    )

    out = dedupe_retail_items(df)

    assert out["item_id"].tolist() == ["return-1"]
    assert out.loc[0, "item_subtotal"] == Decimal("-19.96")
    assert out.loc[0, "allocated_total"] == Decimal("-19.96")
    assert "item_subtotal set to source_order_total" in out.loc[0, "dedupe_notes"]
