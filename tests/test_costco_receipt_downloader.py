from pathlib import Path
from decimal import Decimal

from finance_pipeline.loaders import costco_receipt_downloader


def test_costco_receipt_downloader_json():
    df = costco_receipt_downloader.load(Path("tests/fixtures/costco.json"), "batch")
    assert len(df) == 1
    assert df.loc[0, "receipt_id"] == "COST-1"
    assert df.loc[0, "allocated_total"] == Decimal("13.07")
