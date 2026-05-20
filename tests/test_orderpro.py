from pathlib import Path
import shutil
from decimal import Decimal

from finance_pipeline.loaders import orderpro


def test_orderpro_multiple_stores(tmp_path):
    target = tmp_path / "target"
    amazon = tmp_path / "amazon"
    target.mkdir()
    amazon.mkdir()
    for name in ["orderpro_target_orders.csv", "orderpro_target_items.csv"]:
        shutil.copy(Path("tests/fixtures") / name, target / name)
    for name in ["orderpro_amazon_orders.csv", "orderpro_amazon_items.csv"]:
        shutil.copy(Path("tests/fixtures") / name, amazon / name)

    target_df = orderpro.load(target, "batch", "target")
    amazon_df = orderpro.load(amazon, "batch", "amazon")

    assert len(target_df) == 2
    assert len(amazon_df) == 1
    assert set(target_df["retailer"]) == {"target"}
    assert set(amazon_df["retailer"]) == {"amazon"}
    assert "source_category_raw" in target_df.columns
    assert target_df["allocated_total"].sum() == Decimal("18.50")
