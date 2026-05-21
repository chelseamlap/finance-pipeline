from __future__ import annotations

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
