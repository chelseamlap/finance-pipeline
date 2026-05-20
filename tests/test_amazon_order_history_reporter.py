from pathlib import Path
from decimal import Decimal

from finance_pipeline.loaders import amazon_order_history_reporter


def test_amazon_order_history_reporter_loads_and_allocates_tax():
    df = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    assert len(df) == 2
    assert df["allocated_tax"].sum() == Decimal("0.98")
    assert df["allocated_total"].sum() == Decimal("15.98")
