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
    monthly_rule_coverage = _category_rule_coverage(retail, category_rule_coverage)

    _write(tx, out_dir / "canonical_transactions.csv", TRANSACTION_COLUMNS)
    _write(retail, out_dir / "canonical_retail_items.csv", RETAIL_ITEM_COLUMNS)
    _write(monthly_category_summary(retail), out_dir / "monthly_category_summary.csv")
    _write(retailer_summary(retail), out_dir / "retailer_summary.csv")
    _write(store_reconciliation_summary(tx, retail, reconciliation_detail), out_dir / "store_reconciliation_summary.csv")
    _write(reconciliation_summary, out_dir / "reconciliation_summary.csv")
    _write(reconciliation_detail, out_dir / "reconciliation_detail.csv")
    _write(unmatched_simplifi, out_dir / "unmatched_simplifi_transactions.csv")
    _write(unmatched_retail_orders, out_dir / "unmatched_retail_orders.csv")
    _write(items_needing_review, out_dir / "items_needing_review.csv")
    _write(monthly_rule_coverage, out_dir / "category_rule_coverage.csv")


def _filter_by_month(df: pd.DataFrame, date_column: str, month: str) -> pd.DataFrame:
    out = df.copy()
    if out.empty or date_column not in out.columns:
        return out
    return out[month_mask(out[date_column], month)].copy()


def _reconciliation_summary(detail: pd.DataFrame, items_needing_review: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        retail_orders = 0
        matched_orders = 0
        no_bank_orders = 0
        unmatched_orders = 0
    else:
        retail_orders = len(detail)
        if "matched_simplifi_transaction_id" in detail.columns:
            matched = detail["matched_simplifi_transaction_id"].fillna("").astype(str).str.strip() != ""
            matched_orders = int(matched.sum())
        else:
            matched_orders = 0
        if "status" in detail.columns:
            status = detail["status"].fillna("").astype(str)
            no_bank_orders = int(status.eq("no_bank_transaction_expected").sum())
            unmatched_orders = int(status.str.contains("unmatched_transaction").sum())
        else:
            no_bank_orders = 0
            unmatched_orders = max(retail_orders - matched_orders, 0)
    return pd.DataFrame(
        [
            {"metric": "retail_orders", "value": retail_orders},
            {"metric": "matched_orders", "value": matched_orders},
            {"metric": "no_bank_transaction_expected_orders", "value": no_bank_orders},
            {"metric": "unmatched_orders", "value": unmatched_orders},
            {"metric": "items_needing_review", "value": len(items_needing_review)},
        ]
    )


def _category_rule_coverage(items: pd.DataFrame, fallback: pd.DataFrame) -> pd.DataFrame:
    columns = ["category_rule_id", "matched_rows"]
    if items.empty or "category_rule_id" not in items.columns:
        if fallback.empty:
            return pd.DataFrame(columns=columns)
        return fallback.copy()
    coverage = (
        items["category_rule_id"]
        .fillna("none")
        .astype(str)
        .replace("", "none")
        .value_counts()
        .rename_axis("category_rule_id")
        .reset_index(name="matched_rows")
        .sort_values("category_rule_id")
        .reset_index(drop=True)
    )
    return coverage[columns]


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


def store_reconciliation_summary(
    transactions: pd.DataFrame,
    items: pd.DataFrame,
    reconciliation_detail: pd.DataFrame,
    tolerance_pct: float = 0.05,
) -> pd.DataFrame:
    columns = [
        "retailer",
        "matched_simplifi_total",
        "item_total",
        "item_vs_matched_simplifi_difference",
        "unmatched_retail_orders",
        "unmatched_retail_item_total",
        "reconciled_item_total",
        "reconciled_gap",
        "reconciled_gap_pct_of_store_simplifi",
        "within_5_percent_of_store_simplifi",
        "unmatched_simplifi_transactions",
        "unmatched_simplifi_total",
    ]
    if items.empty and reconciliation_detail.empty:
        return pd.DataFrame(columns=columns)

    retailers = sorted(
        set(items.get("retailer", pd.Series(dtype=str)).dropna().astype(str))
        | set(reconciliation_detail.get("retailer", pd.Series(dtype=str)).dropna().astype(str))
    )
    if not retailers:
        return pd.DataFrame(columns=columns)

    tx = transactions.copy()
    if not tx.empty:
        tx["amount"] = pd.to_numeric(tx.get("amount"), errors="coerce").fillna(0.0)
        tx["merchant_normalized"] = tx.get("merchant_normalized", pd.Series("", index=tx.index)).fillna("").astype(str)

    item_totals = _item_totals_by_retailer(items)
    detail = reconciliation_detail.copy()
    if not detail.empty:
        detail["item_derived_total"] = pd.to_numeric(detail.get("item_derived_total"), errors="coerce").fillna(0.0)
        detail["simplifi_reconciled_total"] = pd.to_numeric(detail.get("simplifi_reconciled_total"), errors="coerce").fillna(0.0)
        detail["status"] = detail.get("status", pd.Series("", index=detail.index)).fillna("").astype(str)

    rows = []
    for retailer in retailers:
        retailer_detail = detail[detail["retailer"].astype(str).eq(retailer)].copy() if not detail.empty and "retailer" in detail.columns else pd.DataFrame()
        item_total = round(float(item_totals.get(retailer, 0.0)), 2)
        matched_simplifi_total = _matched_simplifi_total_for_retailer(retailer_detail)
        unmatched_simplifi_total = _unmatched_simplifi_total(tx, retailer, detail)
        unmatched_detail = _retailer_unmatched_detail(detail, retailer)
        unmatched_retail_total = round(float(unmatched_detail["item_derived_total"].sum()), 2) if not unmatched_detail.empty else 0.0
        reconciled_item_total = round(item_total - unmatched_retail_total, 2)
        item_difference = round(item_total - matched_simplifi_total, 2)
        reconciled_gap = round(reconciled_item_total - matched_simplifi_total, 2)
        gap_pct = round(abs(reconciled_gap) / abs(matched_simplifi_total), 4) if matched_simplifi_total else None
        threshold = abs(matched_simplifi_total) * tolerance_pct
        rows.append(
            {
                "retailer": retailer,
                "matched_simplifi_total": matched_simplifi_total,
                "item_total": item_total,
                "item_vs_matched_simplifi_difference": item_difference,
                "unmatched_retail_orders": len(unmatched_detail),
                "unmatched_retail_item_total": unmatched_retail_total,
                "reconciled_item_total": reconciled_item_total,
                "reconciled_gap": reconciled_gap,
                "reconciled_gap_pct_of_store_simplifi": gap_pct,
                "within_5_percent_of_store_simplifi": abs(reconciled_gap) <= threshold,
                "unmatched_simplifi_transactions": _unmatched_simplifi_count(tx, retailer, detail),
                "unmatched_simplifi_total": unmatched_simplifi_total,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _item_totals_by_retailer(items: pd.DataFrame) -> dict[str, float]:
    if items.empty or "retailer" not in items.columns:
        return {}
    out = items.copy()
    out["allocated_total"] = pd.to_numeric(out.get("allocated_total"), errors="coerce").fillna(0.0)
    return out.groupby("retailer", dropna=False)["allocated_total"].sum().to_dict()


def _matched_simplifi_total_for_retailer(detail: pd.DataFrame) -> float:
    if detail.empty or "matched_simplifi_transaction_id" not in detail.columns:
        return 0.0
    matched = detail[detail["matched_simplifi_transaction_id"].fillna("").astype(str).str.strip() != ""]
    return round(float(matched.get("simplifi_reconciled_total", pd.Series(dtype=float)).sum()), 2)


def _retailer_unmatched_detail(detail: pd.DataFrame, retailer: str) -> pd.DataFrame:
    if detail.empty or "retailer" not in detail.columns:
        return pd.DataFrame(columns=detail.columns)
    status = detail.get("status", pd.Series("", index=detail.index)).fillna("").astype(str)
    return detail[detail["retailer"].astype(str).eq(retailer) & status.str.contains("unmatched_transaction")].copy()


def _matched_transaction_ids(detail: pd.DataFrame) -> set[str]:
    if detail.empty or "matched_simplifi_transaction_id" not in detail.columns:
        return set()
    ids = detail["matched_simplifi_transaction_id"].fillna("").astype(str).str.strip()
    return set(ids[ids != ""])


def _unmatched_simplifi_for_retailer(transactions: pd.DataFrame, retailer: str, detail: pd.DataFrame) -> pd.DataFrame:
    if transactions.empty or "transaction_id" not in transactions.columns:
        return pd.DataFrame(columns=transactions.columns)
    matched_ids = _matched_transaction_ids(detail)
    tx = transactions[transactions["merchant_normalized"].str.contains(retailer, case=False, na=False)].copy()
    return tx[~tx["transaction_id"].astype(str).isin(matched_ids)]


def _unmatched_simplifi_count(transactions: pd.DataFrame, retailer: str, detail: pd.DataFrame) -> int:
    return len(_unmatched_simplifi_for_retailer(transactions, retailer, detail))


def _unmatched_simplifi_total(transactions: pd.DataFrame, retailer: str, detail: pd.DataFrame) -> float:
    unmatched = _unmatched_simplifi_for_retailer(transactions, retailer, detail)
    if unmatched.empty:
        return 0.0
    return round(float((-unmatched["amount"]).sum()), 2)
