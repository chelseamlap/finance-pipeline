from __future__ import annotations

from pathlib import Path

import pandas as pd

from .identity import item_mapping_keys_for_retail_item
from .models import RETAIL_ITEM_COLUMNS, TRANSACTION_COLUMNS
from .normalize import month_mask


def write_period_outputs(
    months: list[str],
    processed_root: Path,
    transactions: pd.DataFrame,
    items: pd.DataFrame,
    reconciliation: dict[str, pd.DataFrame],
    category_rule_coverage: pd.DataFrame,
) -> None:
    for month in months:
        write_month_outputs(month, processed_root / month, transactions, items, reconciliation, category_rule_coverage)


def write_review_outputs(
    out_dir: Path,
    months: list[str],
    transactions: pd.DataFrame,
    items: pd.DataFrame,
    reconciliation: dict[str, pd.DataFrame],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write(run_summary(months, transactions, reconciliation), out_dir / "run_summary.csv")
    _write(category_review(items), out_dir / "category_review.csv")
    _write(reconciliation_review(reconciliation.get("reconciliation_detail", pd.DataFrame())), out_dir / "reconciliation_review.csv")


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


def run_summary(months: list[str], transactions: pd.DataFrame, reconciliation: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    items = reconciliation.get("items", pd.DataFrame())
    detail = reconciliation.get("reconciliation_detail", pd.DataFrame())
    unmatched_simplifi = reconciliation.get("unmatched_simplifi_transactions", pd.DataFrame())
    unmatched_orders = reconciliation.get("unmatched_retail_orders", pd.DataFrame())
    review = reconciliation.get("items_needing_review", pd.DataFrame())
    for month in months:
        month_tx = _filter_by_month(transactions, "posted_date", month)
        month_items = _filter_by_month(items, "transaction_date", month)
        month_detail = _filter_by_month(detail, "transaction_date", month)
        month_review = _filter_by_month(review, "transaction_date", month)
        rows.append(
            {
                "month": month,
                "transactions": len(month_tx),
                "retail_items": len(month_items),
                "retail_orders": _nunique_orders(month_detail),
                "items_needing_review": len(month_review),
                "unknown_category_items": _unknown_category_count(month_items),
                "total_mismatch_orders": _status_count(month_detail, "total_mismatch"),
                "unmatched_transactions": len(_filter_by_month(unmatched_simplifi, "posted_date", month)),
                "unmatched_retail_orders": len(_filter_by_month(unmatched_orders, "transaction_date", month)),
            }
        )
    return pd.DataFrame(rows)


def category_review(items: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "mapping_type",
        "mapping_key",
        "reason",
        "suggested_category",
        "item_count",
        "order_count",
        "total_allocated",
        "first_date",
        "last_date",
        "retailers",
        "source_adapters",
        "sample_original_descriptions",
        "sample_normalized_descriptions",
        "sample_item_ids",
        "sample_order_ids",
        "review_priority",
    ]
    if items.empty:
        return pd.DataFrame(columns=columns)
    df = items.copy()
    review_mask = df.get("needs_review", pd.Series(False, index=df.index)).fillna(False)
    unknown_mask = df.get("household_category", pd.Series("", index=df.index)).fillna("").astype(str).eq("Unknown_Review")
    df = df[review_mask | unknown_mask].copy()
    if df.empty:
        return pd.DataFrame(columns=columns)
    df["allocated_total"] = _numeric_column(df, "allocated_total")
    df["_date"] = pd.to_datetime(df.get("transaction_date"), errors="coerce")
    rows = []
    for _, row in df.iterrows():
        mapping_type, mapping_key = _first_item_mapping_key(row.to_dict())
        reason = _category_review_reason(row)
        rows.append(
            {
                "_mapping_type": mapping_type,
                "_mapping_key": mapping_key,
                "_reason": reason,
                "_suggested_category": _suggested_category(row),
                **row.to_dict(),
            }
        )
    keyed = pd.DataFrame(rows)
    grouped = keyed.groupby(["_mapping_type", "_mapping_key", "_reason", "_suggested_category"], dropna=False)
    out_rows = []
    for (mapping_type, mapping_key, reason, suggested), group in grouped:
        dates = group["_date"].dropna()
        out_rows.append(
            {
                "mapping_type": mapping_type,
                "mapping_key": mapping_key,
                "reason": reason,
                "suggested_category": suggested,
                "item_count": len(group),
                "order_count": group.get("order_id", pd.Series(dtype=str)).fillna("").astype(str).nunique(),
                "total_allocated": round(float(group["allocated_total"].sum()), 2),
                "first_date": dates.min().date().isoformat() if not dates.empty else "",
                "last_date": dates.max().date().isoformat() if not dates.empty else "",
                "retailers": _sample_values(group, "retailer", limit=5),
                "source_adapters": _sample_values(group, "source_adapter", limit=5),
                "sample_original_descriptions": _sample_values(group, "item_description_raw", limit=4),
                "sample_normalized_descriptions": _sample_values(group, "item_description_normalized", limit=4),
                "sample_item_ids": _sample_values(group, "item_id", limit=5),
                "sample_order_ids": _sample_values(group, "order_id", limit=5),
                "review_priority": _review_priority(reason, len(group), float(group["allocated_total"].abs().sum())),
            }
        )
    return pd.DataFrame(out_rows, columns=columns).sort_values(
        ["review_priority", "total_allocated", "item_count"],
        ascending=[True, False, False],
    )


def reconciliation_review(detail: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "retailer",
        "order_id",
        "transaction_date",
        "status",
        "mismatch_diagnostic",
        "item_derived_total",
        "retailer_source_grand_total",
        "simplifi_reconciled_total",
        "item_vs_retailer_difference",
        "item_vs_simplifi_difference",
        "matched_simplifi_transaction_id",
        "review_priority",
    ]
    if detail.empty:
        return pd.DataFrame(columns=columns)
    df = detail.copy()
    status = df.get("status", pd.Series("", index=df.index)).fillna("").astype(str)
    df = df[status.str.contains("total_mismatch|unmatched_transaction", regex=True)].copy()
    if df.empty:
        return pd.DataFrame(columns=columns)
    for column in ["item_vs_retailer_difference", "item_vs_simplifi_difference", "item_derived_total"]:
        df[column] = _numeric_column(df, column)
    df["review_priority"] = df.apply(_reconciliation_priority, axis=1)
    out = df.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = ""
    out["_abs_gap"] = out[["item_vs_retailer_difference", "item_vs_simplifi_difference"]].abs().max(axis=1)
    return out.sort_values(["review_priority", "_abs_gap"], ascending=[True, False])[columns]


def _filter_by_month(df: pd.DataFrame, date_column: str, month: str) -> pd.DataFrame:
    out = df.copy()
    if out.empty or date_column not in out.columns:
        return out
    return out[month_mask(out[date_column], month)].copy()


def _numeric_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[column], errors="coerce").fillna(0.0)


def _nunique_orders(detail: pd.DataFrame) -> int:
    if detail.empty or "order_id" not in detail.columns:
        return 0
    return int(detail["order_id"].fillna("").astype(str).nunique())


def _unknown_category_count(items: pd.DataFrame) -> int:
    if items.empty or "household_category" not in items.columns:
        return 0
    return int(items["household_category"].fillna("").astype(str).eq("Unknown_Review").sum())


def _status_count(detail: pd.DataFrame, needle: str) -> int:
    if detail.empty or "status" not in detail.columns:
        return 0
    return int(detail["status"].fillna("").astype(str).str.contains(needle, regex=False).sum())


def _first_item_mapping_key(row: dict) -> tuple[str, str]:
    keys = item_mapping_keys_for_retail_item(row)
    if keys:
        return keys[0]
    item_id = str(row.get("item_id", "") or "").strip()
    if item_id:
        return "item_id", item_id
    return "unknown", ""


def _category_review_reason(row: pd.Series) -> str:
    category = str(row.get("household_category", "") or "")
    reason = str(row.get("review_reason", "") or "").strip()
    if category == "Unknown_Review":
        return "unknown_category"
    if "mapping_conflict" in reason:
        return "mapping_conflict"
    if "total_mismatch" in reason:
        return "reconciliation_total_mismatch"
    if "unmatched_transaction" in reason:
        return "reconciliation_unmatched_transaction"
    return reason or "needs_review"


def _suggested_category(row: pd.Series) -> str:
    category = str(row.get("household_category", "") or "").strip()
    return "" if category == "Unknown_Review" else category


def _sample_values(group: pd.DataFrame, column: str, limit: int) -> str:
    if column not in group.columns:
        return ""
    values = []
    for value in group[column].fillna("").astype(str):
        value = value.strip()
        if not value or value in values:
            continue
        values.append(value)
        if len(values) >= limit:
            break
    return " | ".join(values)


def _review_priority(reason: str, item_count: int, absolute_total: float) -> int:
    if reason == "mapping_conflict":
        return 1
    if reason == "unknown_category" and (item_count >= 3 or absolute_total >= 50):
        return 2
    if reason == "unknown_category":
        return 3
    if reason.startswith("reconciliation_"):
        return 4
    return 5


def _reconciliation_priority(row: pd.Series) -> int:
    status = str(row.get("status", "") or "")
    diagnostic = str(row.get("mismatch_diagnostic", "") or "")
    if "total_mismatch" in status and "unmatched_transaction" in status:
        return 1
    if "component_mismatch" in diagnostic:
        return 2
    if "total_mismatch" in status:
        return 3
    if "unmatched_transaction" in status:
        return 4
    return 5


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
