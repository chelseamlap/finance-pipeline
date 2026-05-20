from pathlib import Path

from finance_pipeline.loaders import amazon_order_history_exporter


def test_amazon_order_history_exporter_json():
    df = amazon_order_history_exporter.load(Path("tests/fixtures/amazon_exporter.json"), "batch")
    assert len(df) == 1
    assert df.loc[0, "order_id"] == "AMZ-JSON-1"
    assert df.loc[0, "retailer"] == "amazon"
