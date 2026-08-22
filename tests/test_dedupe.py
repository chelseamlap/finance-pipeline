from __future__ import annotations

import pandas as pd

from finance_pipeline.dedupe import dedupe_retail_items


def test_dedupe_retail_items_prefers_higher_priority_adapter_for_same_order():
    df = pd.DataFrame(
        [
            {"item_id": "reporter-1", "retailer": "amazon", "order_id": "A-1", "source_adapter": "amazon_order_history_reporter", "allocated_total": 10},
            {"item_id": "reporter-2", "retailer": "amazon", "order_id": "A-1", "source_adapter": "amazon_order_history_reporter", "allocated_total": 5},
            {"item_id": "legacy-1", "retailer": "amazon", "order_id": "A-1", "source_adapter": "amazon_manual_export", "allocated_total": 99},
            {"item_id": "target-1", "retailer": "target", "order_id": "T-1", "source_adapter": "store_receipt_extract", "allocated_total": 20},
        ]
    )

    out = dedupe_retail_items(df)

    assert out["item_id"].tolist() == ["reporter-1", "reporter-2", "target-1"]
    amazon = out[out["retailer"] == "amazon"]
    assert set(amazon["source_adapter"]) == {"amazon_order_history_reporter"}
    assert amazon["dedupe_notes"].str.contains("dropped amazon_manual_export").all()


def test_dedupe_retail_items_keeps_distinct_orders_with_no_adapter_conflict():
    df = pd.DataFrame(
        [
            {"item_id": "legacy-1", "retailer": "amazon", "order_id": "A-2", "source_adapter": "amazon_manual_export", "allocated_total": 12},
            {"item_id": "reporter-1", "retailer": "amazon", "order_id": "A-3", "source_adapter": "amazon_order_history_reporter", "allocated_total": 15},
        ]
    )

    out = dedupe_retail_items(df)

    assert out["item_id"].tolist() == ["legacy-1", "reporter-1"]
    assert out["dedupe_notes"].fillna("").eq("").all()
