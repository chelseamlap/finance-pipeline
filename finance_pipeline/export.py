from __future__ import annotations

from pathlib import Path

import pandas as pd

from .models import RETAIL_ITEM_COLUMNS, TRANSACTION_COLUMNS
from .normalize import month_mask


def write_month_outputs(
    month: str,
    out_dir: Path,
    transactions: pd.DataFrame,
    items: pd.DataFrame,
    reconciliation: dict[str, pd.DataFrame],
    category_rule_coverage: pd.DataFrame,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    tx = transactions.copy()
    retail = reconciliation.get("items", items).copy()
    if not tx.empty:
        tx = tx[month_mask(tx["posted_date"], month)]
    if not retail.empty:
        retail = retail[month_mask(retail["transaction_date"], month)]

    _write(tx, out_dir / "canonical_transactions.csv", TRANSACTION_COLUMNS)
    _write(retail, out_dir / "canonical_retail_items.csv", RETAIL_ITEM_COLUMNS)
    _write(monthly_category_summary(retail), out_dir / "monthly_category_summary.csv")
    _write(retailer_summary(retail), out_dir / "retailer_summary.csv")
    for name in [
        "reconciliation_summary",
        "reconciliation_detail",
        "unmatched_simplifi_transactions",
        "unmatched_retail_orders",
        "items_needing_review",
    ]:
        _write(reconciliation.get(name, pd.DataFrame()), out_dir / f"{name}.csv")
    _write(category_rule_coverage, out_dir / "category_rule_coverage.csv")


def _write(df: pd.DataFrame, path: Path, columns: list[str] | None = None) -> None:
    out = df.copy()
    if columns is not None:
        for col in columns:
            if col not in out.columns:
                out[col] = ""
        out = out[columns]
    out.to_csv(path, index=False)


def monthly_category_summary(items: pd.DataFrame) -> pd.DataFrame:
    if items.empty:
        return pd.DataFrame(columns=["household_category", "allocated_total"])
    return items.groupby("household_category", dropna=False)["allocated_total"].sum().reset_index()


def retailer_summary(items: pd.DataFrame) -> pd.DataFrame:
    if items.empty:
        return pd.DataFrame(columns=["retailer", "orders", "allocated_total"])
    return (
        items.groupby("retailer", dropna=False)
        .agg(orders=("order_id", "nunique"), allocated_total=("allocated_total", "sum"))
        .reset_index()
    )
