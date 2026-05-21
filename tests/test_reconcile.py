from pathlib import Path
from decimal import Decimal

from finance_pipeline.categorize import categorize_items
from finance_pipeline.loaders import amazon_order_history_reporter, simplifi
from finance_pipeline.reconcile import reconcile


def test_totals_reconcile_and_match_simplifi():
    tx = simplifi.load(Path("tests/fixtures/simplifi.csv"), "batch")
    items = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    items, _ = categorize_items(items)
    rec = reconcile(tx, items)
    detail = rec["reconciliation_detail"]
    assert detail.loc[0, "item_vs_retailer_difference"] == 0
    assert detail.loc[0, "matched_simplifi_transaction_id"] == "txn-amz-1"
    assert detail.loc[0, "simplifi_amount"] == Decimal("-15.98")
    assert detail.loc[0, "simplifi_reconciled_total"] == Decimal("15.98")
    assert detail.loc[0, "item_derived_total"] == Decimal("15.98")
    assert detail.loc[0, "retailer_source_grand_total"] == Decimal("15.98")
    assert detail.loc[0, "item_vs_simplifi_difference"] == Decimal("0.00")
    assert detail.loc[0, "retailer_vs_simplifi_difference"] == Decimal("0.00")


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
    assert detail["mismatch_diagnostic"] == "single_item_base_higher_than_source_after_components"
    assert detail["base_difference_after_components"] == Decimal("24.30")
    assert detail["item_derived_total"] == Decimal("122.44")
    assert detail["retailer_source_grand_total"] == Decimal("98.14")
    assert detail["simplifi_amount"] == Decimal("-98.14")
    assert detail["simplifi_reconciled_total"] == Decimal("98.14")
    assert detail["item_vs_simplifi_difference"] == Decimal("24.30")
    assert detail["retailer_vs_simplifi_difference"] == Decimal("0.00")
    assert detail["item_vs_retailer_difference"] == Decimal("24.30")
    assert "item_derived_total=122.44" in detail["mismatch_basis"]
    assert "retailer_source_grand_total=98.14" in detail["mismatch_basis"]
    assert "item_vs_retailer_difference=24.30" in detail["mismatch_basis"]
    assert "base_difference_after_components=24.30" in detail["mismatch_basis"]
    assert rec["items"].iloc[0]["review_reason"] == "unknown category; total_mismatch: single_item_base_higher_than_source_after_components"


def test_component_consistency_excludes_shipping_when_grand_total_matches_without_it():
    import pandas as pd

    transactions = pd.DataFrame(
        [
            {
                "transaction_id": "txn-amz-shipping-gap",
                "posted_date": "2026-04-29",
                "merchant_normalized": "amazon",
                "amount": -26.05,
            }
        ]
    )
    items = pd.DataFrame(
        [
            {
                "retailer": "amazon",
                "order_id": "order-shipping",
                "transaction_date": "2026-04-27",
                "merchant_normalized": "amazon",
                "item_subtotal": 24.82,
                "allocated_tax": 1.23,
                "allocated_shipping": 2.24,
                "allocated_fee": 0,
                "item_discount": 0,
                "allocated_total": 28.29,
                "source_tax_total": 1.23,
                "source_shipping_total": 2.24,
                "source_discount_total": 0,
                "source_grand_total": 26.05,
                "needs_review": False,
                "review_reason": "",
                "household_category": "Home_Improvement",
            }
        ]
    )

    rec = reconcile(transactions, items)
    detail = rec["reconciliation_detail"].iloc[0]

    assert detail["status"] == "ok"
    assert detail["item_vs_retailer_difference"] == 0
    assert detail["matched_simplifi_transaction_id"] == "txn-amz-shipping-gap"
    assert rec["items"].iloc[0]["allocated_shipping"] == Decimal("0.00")
    assert rec["items"].iloc[0]["allocated_total"] == Decimal("26.05")
    assert "source_shipping_total_excluded" in rec["items"].iloc[0]["component_allocation_notes"]
    assert not rec["items"].iloc[0]["needs_review"]


def test_component_consistency_chooses_matching_subset_from_multiple_components():
    import pandas as pd

    transactions = pd.DataFrame(
        [
            {
                "transaction_id": "txn-component-subset",
                "posted_date": "2026-04-29",
                "merchant_normalized": "amazon",
                "amount": -23.00,
            }
        ]
    )
    items = pd.DataFrame(
        [
            {
                "retailer": "amazon",
                "order_id": "order-components",
                "transaction_date": "2026-04-27",
                "merchant_normalized": "amazon",
                "item_subtotal": 20.00,
                "allocated_tax": 0,
                "allocated_shipping": 0,
                "allocated_fee": 0,
                "item_discount": 0,
                "allocated_total": 20.00,
                "source_tax_total": 2.00,
                "source_shipping_total": 5.00,
                "source_fee_total": 1.00,
                "source_discount_total": 0,
                "source_grand_total": 23.00,
                "needs_review": False,
                "review_reason": "",
                "household_category": "Home_Improvement",
            }
        ]
    )

    rec = reconcile(transactions, items)
    item = rec["items"].iloc[0]
    detail = rec["reconciliation_detail"].iloc[0]

    assert detail["status"] == "ok"
    assert item["allocated_tax"] == Decimal("2.00")
    assert item["allocated_fee"] == Decimal("1.00")
    assert item["allocated_shipping"] == Decimal("0.00")
    assert item["allocated_total"] == Decimal("23.00")
    assert "source_shipping_total_excluded" in item["component_allocation_notes"]


def test_negative_item_discount_is_normalized_to_positive_amount_to_subtract():
    import pandas as pd

    transactions = pd.DataFrame(
        [
            {
                "transaction_id": "txn-item-discount",
                "posted_date": "2026-04-29",
                "merchant_normalized": "target",
                "amount": -15.00,
            }
        ]
    )
    items = pd.DataFrame(
        [
            {
                "retailer": "target",
                "order_id": "order-item-discount",
                "transaction_date": "2026-04-27",
                "merchant_normalized": "target",
                "item_subtotal": 20.00,
                "allocated_tax": 0,
                "allocated_shipping": 0,
                "allocated_fee": 0,
                "item_discount": -5.00,
                "allocated_total": 25.00,
                "source_grand_total": 15.00,
                "needs_review": False,
                "review_reason": "",
                "household_category": "Groceries",
            }
        ]
    )

    rec = reconcile(transactions, items)
    item = rec["items"].iloc[0]
    detail = rec["reconciliation_detail"].iloc[0]

    assert detail["status"] == "ok"
    assert item["item_discount"] == Decimal("5.00")
    assert item["allocated_total"] == Decimal("15.00")
    assert "item_discount_normalized_to_positive_amount_to_subtract" in item["component_allocation_notes"]


def test_negative_source_discount_is_normalized_before_allocation():
    import pandas as pd

    transactions = pd.DataFrame(
        [
            {
                "transaction_id": "txn-source-discount",
                "posted_date": "2026-04-29",
                "merchant_normalized": "target",
                "amount": -15.00,
            }
        ]
    )
    items = pd.DataFrame(
        [
            {
                "retailer": "target",
                "order_id": "order-source-discount",
                "transaction_date": "2026-04-27",
                "merchant_normalized": "target",
                "item_subtotal": 20.00,
                "allocated_tax": 0,
                "allocated_shipping": 0,
                "allocated_fee": 0,
                "item_discount": 0,
                "allocated_total": 20.00,
                "source_discount_total": -5.00,
                "source_grand_total": 15.00,
                "needs_review": False,
                "review_reason": "",
                "household_category": "Groceries",
            }
        ]
    )

    rec = reconcile(transactions, items)
    item = rec["items"].iloc[0]
    detail = rec["reconciliation_detail"].iloc[0]

    assert detail["status"] == "ok"
    assert item["source_discount_total"] == Decimal("5.00")
    assert item["item_discount"] == Decimal("5.00")
    assert item["allocated_total"] == Decimal("15.00")
    assert "source_discount_total_normalized_to_positive_amount_to_subtract" in item["component_allocation_notes"]


def test_positive_retailer_total_does_not_match_simplifi_refund():
    import pandas as pd

    transactions = pd.DataFrame(
        [
            {
                "transaction_id": "txn-costco-refund",
                "posted_date": "2026-01-24",
                "merchant_normalized": "costco",
                "amount": 21.78,
            }
        ]
    )
    items = pd.DataFrame(
        [
            {
                "retailer": "costco",
                "order_id": "costco-charge",
                "transaction_date": "2026-01-24",
                "merchant_normalized": "costco",
                "item_subtotal": 21.78,
                "allocated_total": 21.78,
                "source_grand_total": 21.78,
                "needs_review": False,
                "review_reason": "",
                "household_category": "Groceries",
            }
        ]
    )

    rec = reconcile(transactions, items)
    detail = rec["reconciliation_detail"].iloc[0]

    assert detail["status"] == "unmatched_transaction"
    assert detail["matched_simplifi_transaction_id"] == ""
    assert pd.isna(detail["simplifi_amount"])


def test_negative_retailer_total_matches_simplifi_refund():
    import pandas as pd

    transactions = pd.DataFrame(
        [
            {
                "transaction_id": "txn-costco-refund",
                "posted_date": "2026-01-24",
                "merchant_normalized": "costco",
                "amount": 21.78,
            }
        ]
    )
    items = pd.DataFrame(
        [
            {
                "retailer": "costco",
                "order_id": "costco-refund",
                "transaction_date": "2026-01-24",
                "merchant_normalized": "costco",
                "item_subtotal": -21.78,
                "allocated_total": -21.78,
                "source_grand_total": -21.78,
                "needs_review": False,
                "review_reason": "",
                "household_category": "Groceries",
            }
        ]
    )

    rec = reconcile(transactions, items)
    detail = rec["reconciliation_detail"].iloc[0]

    assert detail["status"] == "ok"
    assert detail["matched_simplifi_transaction_id"] == "txn-costco-refund"
    assert detail["simplifi_amount"] == Decimal("21.78")
    assert detail["simplifi_reconciled_total"] == Decimal("-21.78")
    assert detail["retailer_vs_simplifi_difference"] == Decimal("0.00")


def test_return_placeholder_reconciles_after_source_order_subtotal_override():
    import pandas as pd

    from finance_pipeline.dedupe import dedupe_retail_items

    transactions = pd.DataFrame(
        [
            {
                "transaction_id": "txn-costco-return",
                "posted_date": "2026-01-20",
                "merchant_normalized": "costco",
                "amount": 21.78,
            }
        ]
    )
    items = pd.DataFrame(
        [
            {
                "item_id": f"return-{idx}",
                "retailer": "costco",
                "order_id": "costco-return",
                "source_adapter": "orderpro",
                "transaction_date": "2026-01-20",
                "merchant_normalized": "costco",
                "item_description_normalized": "1899652",
                "item_description_raw": "/1899652",
                "quantity": 1,
                "unit_price": 5.00,
                "item_subtotal": 5.00,
                "allocated_total": 5.00,
                "source_order_total": -19.96,
                "source_tax_total": -1.82,
                "source_discount_total": 5.00,
                "source_grand_total": -21.78,
                "needs_review": False,
                "review_reason": "",
                "household_category": "Groceries",
            }
            for idx in range(2)
        ]
    )

    deduped = dedupe_retail_items(items)
    rec = reconcile(transactions, deduped)
    item = rec["items"].iloc[0]
    detail = rec["reconciliation_detail"].iloc[0]

    assert detail["status"] == "ok"
    assert detail["matched_simplifi_transaction_id"] == "txn-costco-return"
    assert detail["item_derived_total"] == Decimal("-21.78")
    assert item["item_subtotal"] == Decimal("-19.96")
    assert item["item_discount"] == Decimal("0.00")
    assert item["allocated_tax"] == Decimal("-1.82")
    assert "source_discount_total_excluded" in item["component_allocation_notes"]
