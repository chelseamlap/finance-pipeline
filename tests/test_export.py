from __future__ import annotations

import pandas as pd

from finance_pipeline.export import write_month_outputs


def test_write_month_outputs_filters_reconciliation_artifacts(tmp_path):
    transactions = pd.DataFrame(
        [
            {"transaction_id": "tx-apr", "posted_date": "2026-04-30", "merchant_normalized": "target", "amount": -10},
            {"transaction_id": "tx-may", "posted_date": "2026-05-02", "merchant_normalized": "target", "amount": -20},
        ]
    )
    items = pd.DataFrame(
        [
            {"item_id": "item-apr", "retailer": "target", "order_id": "order-apr", "transaction_date": "2026-04-30", "household_category": "Groceries", "allocated_total": 10, "needs_review": False, "category_rule_id": "rule-apr"},
            {"item_id": "item-may", "retailer": "target", "order_id": "order-may", "transaction_date": "2026-05-02", "household_category": "Groceries", "allocated_total": 20, "needs_review": True, "category_rule_id": "rule-may"},
        ]
    )
    detail = pd.DataFrame(
        [
            {"retailer": "target", "order_id": "order-apr", "transaction_date": "2026-04-30", "simplifi_reconciled_total": 10, "item_derived_total": 10, "matched_simplifi_transaction_id": "tx-apr", "status": "ok"},
            {"retailer": "target", "order_id": "order-may", "transaction_date": "2026-05-02", "simplifi_reconciled_total": "", "item_derived_total": 20, "matched_simplifi_transaction_id": "", "status": "unmatched_transaction"},
        ]
    )
    reconciliation = {
        "items": items,
        "reconciliation_detail": detail,
        "unmatched_simplifi_transactions": transactions,
        "unmatched_retail_orders": detail[detail["matched_simplifi_transaction_id"] == ""],
        "items_needing_review": items[items["needs_review"]],
    }

    full_coverage = pd.DataFrame([{"category_rule_id": "rule-apr", "matched_rows": 1}, {"category_rule_id": "rule-may", "matched_rows": 1}])

    write_month_outputs("2026-05", tmp_path, transactions, items, reconciliation, full_coverage)

    assert pd.read_csv(tmp_path / "canonical_transactions.csv")["transaction_id"].tolist() == ["tx-may"]
    assert pd.read_csv(tmp_path / "canonical_retail_items.csv")["item_id"].tolist() == ["item-may"]
    assert pd.read_csv(tmp_path / "reconciliation_detail.csv")["order_id"].tolist() == ["order-may"]
    assert pd.read_csv(tmp_path / "unmatched_simplifi_transactions.csv")["transaction_id"].tolist() == ["tx-may"]
    assert pd.read_csv(tmp_path / "unmatched_retail_orders.csv")["order_id"].tolist() == ["order-may"]
    assert pd.read_csv(tmp_path / "items_needing_review.csv")["item_id"].tolist() == ["item-may"]
    assert pd.read_csv(tmp_path / "category_rule_coverage.csv").to_dict("records") == [{"category_rule_id": "rule-may", "matched_rows": 1}]
    store_summary = pd.read_csv(tmp_path / "store_reconciliation_summary.csv")
    store_summary = store_summary.astype("object").where(pd.notna(store_summary), None)
    assert store_summary.to_dict("records") == [
        {
            "retailer": "target",
            "matched_simplifi_total": 0.0,
            "item_total": 20.0,
            "item_vs_matched_simplifi_difference": 20.0,
            "unmatched_retail_orders": 1,
            "unmatched_retail_item_total": 20.0,
            "reconciled_item_total": 0.0,
            "reconciled_gap": 0.0,
            "reconciled_gap_pct_of_store_simplifi": None,
            "within_5_percent_of_store_simplifi": True,
            "unmatched_simplifi_transactions": 1,
            "unmatched_simplifi_total": 20.0,
        }
    ]

    summary = dict(pd.read_csv(tmp_path / "reconciliation_summary.csv").values)
    assert summary == {
        "retail_orders": 1,
        "matched_orders": 0,
        "no_bank_transaction_expected_orders": 0,
        "unmatched_orders": 1,
        "items_needing_review": 1,
    }
