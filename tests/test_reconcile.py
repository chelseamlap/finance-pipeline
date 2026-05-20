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
