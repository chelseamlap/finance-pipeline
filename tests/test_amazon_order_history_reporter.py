from pathlib import Path
from decimal import Decimal

from finance_pipeline.loaders import amazon_order_history_reporter


def test_amazon_order_history_reporter_loads_and_allocates_tax():
    df = amazon_order_history_reporter.load(Path("tests/fixtures/amazon_ohr.csv"), "batch")
    assert len(df) == 2
    assert df["allocated_tax"].sum() == Decimal("0.98")
    assert df["allocated_total"].sum() == Decimal("15.98")


def test_amazon_order_history_reporter_order_level_export(tmp_path):
    source = tmp_path / "amazon_order_history_2026.csv"
    source.write_text(
        "order id,order url,items,to,date,total,shipping,shipping_refund,gift,tax,refund,payments\n"
        "113-1,https://example.com,Whole Milk; Paper Towels; ,Chelsea,2026-05-20,43.31,0,,,2.05,,Visa\n"
    )

    df = amazon_order_history_reporter.load(source, "batch")

    assert len(df) == 1
    assert df.loc[0, "order_id"] == "113-1"
    assert df.loc[0, "allocated_total"] == Decimal("43.31")
    assert df.loc[0, "source_grand_total"] == Decimal("43.31")
    assert bool(df.loc[0, "needs_review"])
    assert "order-level Amazon reporter export" in df.loc[0, "review_reason"]



def test_amazon_order_history_reporter_combines_order_and_item_exports(tmp_path):
    orders = tmp_path / "amazon_order_history_2026.csv"
    orders.write_text(
        "order id,order url,items,to,date,total,shipping,shipping_refund,gift,tax,refund,payments\n"
        "113-1,https://example.com,Whole Milk; Paper Towels; ,Chelsea,2026-05-20,43.31,0,,,2.05,,Visa\n"
    )
    items = tmp_path / "amazon_order_history_items_2026.csv"
    items.write_text(
        "order id,order url,order date,quantity,description,item url,price,subscribe & save,ASIN,category\n"
        "113-1,https://example.com,2026-05-20,1,Whole Milk,https://example.com/item,32.99,0,B001,Groceries\n"
        "113-1,https://example.com,2026-05-20,1,Paper Towels,https://example.com/item2,8.27,0,B002,Household\n"
    )

    df = amazon_order_history_reporter.load(tmp_path, "batch")

    assert len(df) == 2
    assert set(df["asin"]) == {"B001", "B002"}
    assert df["source_grand_total"].iloc[0] == Decimal("43.31")
    assert df["allocated_tax"].sum() == Decimal("2.05")
    assert df["allocated_total"].sum() == Decimal("43.31")
    assert not df["needs_review"].any()
