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

    tx = _filter_by_month(transactions, "posted_date", month)
    retail = _filter_by_month(reconciliation.get("items", items), "transaction_date", month)
    reconciliation_detail = _filter_by_month(reconciliation.get("reconciliation_detail", pd.DataFrame()), "transaction_date", month)
    unmatched_simplifi = _filter_by_month(reconciliation.get("unmatched_simplifi_transactions", pd.DataFrame()), "posted_date", month)
    unmatched_retail_orders = _filter_by_month(reconciliation.get("unmatched_retail_orders", pd.DataFrame()), "transaction_date", month)
    items_needing_review = _filter_by_month(reconciliation.get("items_needing_review", pd.DataFrame()), "transaction_date", month)
    reconciliation_summary = _reconciliation_summary(reconciliation_detail, items_needing_review)

    _write(tx, out_dir / "canonical_transactions.csv", TRANSACTION_COLUMNS)
    _write(retail, out_dir / "canonical_retail_items.csv", RETAIL_ITEM_COLUMNS)
    _write(monthly_category_summary(retail), out_dir / "monthly_category_summary.csv")
    _write(retailer_summary(retail), out_dir / "retailer_summary.csv")
    _write(reconciliation_summary, out_dir / "reconciliation_summary.csv")
    _write(reconciliation_detail, out_dir / "reconciliation_detail.csv")
    _write(unmatched_simplifi, out_dir / "unmatched_simplifi_transactions.csv")
    _write(unmatched_retail_orders, out_dir / "unmatched_retail_orders.csv")
    _write(items_needing_review, out_dir / "items_needing_review.csv")
    _write(category_rule_coverage, out_dir / "category_rule_coverage.csv")


def _filter_by_month(df: pd.DataFrame, date_column: str, month: str) -> pd.DataFrame:
    out = df.copy()
    if out.empty or date_column not in out.columns:
        return out
    return out[month_mask(out[date_column], month)].copy()


def _reconciliation_summary(detail: pd.DataFrame, items_needing_review: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        retail_orders = 0
        matched_orders = 0
    else:
        retail_orders = len(detail)
        if "matched_simplifi_transaction_id" in detail.columns:
            matched = detail["matched_simplifi_transaction_id"].fillna("").astype(str).str.strip() != ""
            matched_orders = int(matched.sum())
        else:
            matched_orders = 0
    return pd.DataFrame(
        [
            {"metric": "retail_orders", "value": retail_orders},
            {"metric": "matched_orders", "value": matched_orders},
            {"metric": "unmatched_orders", "value": max(retail_orders - matched_orders, 0)},
            {"metric": "items_needing_review", "value": len(items_needing_review)},
        ]
    )


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
