from pathlib import Path

from finance_pipeline.categorize import categorize_items
from finance_pipeline.loaders import amazon_order_history_reporter, simplifi
from finance_pipeline.reconcile import reconcile


def test_totals_reconcile_and_match_simplifi():
    tx = simplifi.load(Path("tests/fixtures/simplifi.csv"), "batch")
    items = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    items, _ = categorize_items(items)
    rec = reconcile(tx, items)
    detail = rec["reconciliation_detail"]
    assert detail.loc[0, "difference"] == 0
    assert detail.loc[0, "matched_simplifi_transaction_id"] == "txn-amz-1"


def test_no_rows_silently_dropped_for_malformed_file(tmp_path):
    from finance_pipeline.loaders import amazon_order_history_reporter
    import shutil

    src = tmp_path / "bad.csv"
    shutil.copy(Path("tests/fixtures/malformed.csv"), src)
    df = amazon_order_history_reporter.load(src, "batch")
    assert df.empty


def test_total_mismatch_can_still_match_simplifi_by_source_grand_total():
    import pandas as pd

    transactions = pd.DataFrame(
        [
            {
                "transaction_id": "txn-amz-source-total",
                "posted_date": "2026-04-26",
                "merchant_normalized": "amazon",
                "amount": -98.14,
            }
        ]
    )
    items = pd.DataFrame(
        [
            {
                "retailer": "amazon",
                "order_id": "order-1",
                "transaction_date": "2026-04-22",
                "merchant_normalized": "amazon",
                "item_subtotal": 122.44,
                "allocated_total": 122.44,
                "source_grand_total": 98.14,
                "needs_review": False,
                "review_reason": "unknown category; unmatched_transaction",
                "household_category": "Unknown_Review",
            }
        ]
    )

    rec = reconcile(transactions, items)
    detail = rec["reconciliation_detail"].iloc[0]

    assert detail["status"] == "total_mismatch"
    assert detail["matched_simplifi_transaction_id"] == "txn-amz-source-total"
    assert rec["unmatched_retail_orders"].empty
    assert rec["unmatched_simplifi_transactions"].empty
    assert rec["items"].iloc[0]["review_reason"] == "unknown category; total_mismatch"
