from __future__ import annotations

from decimal import Decimal

import pandas as pd

from .categorize import append_reason
from .models import TWOPLACES, money
from .normalize import normalize_merchant


def _dec(value: object) -> Decimal:
    if value is None or str(value).strip() in {"", "NaN", "nan", "None"}:
        return Decimal("0.00")
    return money(value)


def allocate_order_amounts(df: pd.DataFrame, tolerance: Decimal = Decimal("0.03")) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col in ["allocated_tax", "allocated_shipping", "allocated_fee", "item_discount"]:
        if col not in out.columns:
            out[col] = Decimal("0.00")

    group_keys = ["retailer", "order_id"]
    for keys, group in out.groupby(group_keys, dropna=False):
        indices = list(group.index)
        positive_subtotals = [_dec(out.at[i, "item_subtotal"]) if _dec(out.at[i, "item_subtotal"]) > 0 else Decimal("0") for i in indices]
        base = sum(positive_subtotals, Decimal("0"))

        for source_col, alloc_col in [
            ("source_tax_total", "allocated_tax"),
            ("source_shipping_total", "allocated_shipping"),
            ("source_fee_total", "allocated_fee"),
        ]:
            source_total = first_decimal(group, source_col)
            current_total = sum((_dec(out.at[i, alloc_col]) for i in indices), Decimal("0"))
            if source_total is not None and abs(current_total) <= tolerance and source_total != 0:
                _allocate(out, indices, positive_subtotals, base, source_total, alloc_col)

        source_discount = first_decimal(group, "source_discount_total")
        current_discount = sum((_dec(out.at[i, "item_discount"]) for i in indices), Decimal("0"))
        if source_discount is not None and abs(current_discount) <= tolerance and source_discount != 0:
            _allocate(out, indices, positive_subtotals, base, abs(source_discount), "item_discount")

    out["allocated_total"] = out.apply(
        lambda row: (_dec(row.get("item_subtotal")) - _dec(row.get("item_discount")) + _dec(row.get("allocated_tax")) + _dec(row.get("allocated_shipping")) + _dec(row.get("allocated_fee"))).quantize(TWOPLACES),
        axis=1,
    )
    return out


def _allocate(out: pd.DataFrame, indices: list[int], bases: list[Decimal], base_total: Decimal, amount: Decimal, column: str) -> None:
    if not indices:
        return
    if base_total == 0:
        shares = [Decimal("0") for _ in indices]
        shares[0] = amount
    else:
        running = Decimal("0")
        shares = []
        for base in bases[:-1]:
            share = (amount * base / base_total).quantize(TWOPLACES)
            shares.append(share)
            running += share
        shares.append((amount - running).quantize(TWOPLACES))
    for idx, share in zip(indices, shares):
        out.at[idx, column] = share


def first_decimal(group: pd.DataFrame, column: str) -> Decimal | None:
    if column not in group.columns:
        return None
    for value in group[column]:
        if value is not None and str(value).strip() not in {"", "nan", "NaN", "None"}:
            return _dec(value)
    return None


def reconcile(
    transactions: pd.DataFrame,
    items: pd.DataFrame,
    date_window_days: int = 5,
    amount_tolerance: Decimal = Decimal("0.03"),
) -> dict[str, pd.DataFrame]:
    items = allocate_order_amounts(items, amount_tolerance) if not items.empty else items
    detail_rows: list[dict] = []
    order_rows = _orders(items)
    matched_txn_ids: set[str] = set()
    matched_order_keys: set[tuple[str, str]] = set()

    for _, order in order_rows.iterrows():
        calc = _dec(order["calculated_total"])
        source_grand = order.get("source_grand_total")
        source_total = _dec(source_grand) if pd.notna(source_grand) and str(source_grand) != "" else None
        diff = (calc - source_total).quantize(TWOPLACES) if source_total is not None else Decimal("0.00")
        status = "ok" if source_total is None or abs(diff) <= amount_tolerance else "total_mismatch"

        match_id = ""
        if not transactions.empty:
            match_id = _match_transaction(transactions, order, amount_tolerance, date_window_days, matched_txn_ids)
        if match_id:
            matched_txn_ids.add(match_id)
            matched_order_keys.add((str(order["retailer"]), str(order["order_id"])))
            items.loc[(items["retailer"] == order["retailer"]) & (items["order_id"] == order["order_id"]), "matched_simplifi_transaction_id"] = match_id
        else:
            status = "unmatched_transaction" if status == "ok" else f"{status}; unmatched_transaction"

        if "total_mismatch" in status or "unmatched_transaction" in status:
            mask = (items["retailer"] == order["retailer"]) & (items["order_id"] == order["order_id"])
            items.loc[mask, "needs_review"] = True
            items.loc[mask, "review_reason"] = items.loc[mask, "review_reason"].apply(lambda value: append_reason(value, status))

        detail_rows.append(
            {
                "retailer": order["retailer"],
                "order_id": order["order_id"],
                "transaction_date": order["transaction_date"],
                "calculated_total": calc,
                "source_grand_total": source_total,
                "difference": diff,
                "matched_simplifi_transaction_id": match_id,
                "status": status,
            }
        )

    detail = pd.DataFrame(detail_rows)
    summary = pd.DataFrame(
        [
            {"metric": "retail_orders", "value": len(order_rows)},
            {"metric": "matched_orders", "value": len(matched_order_keys)},
            {"metric": "unmatched_orders", "value": max(len(order_rows) - len(matched_order_keys), 0)},
            {"metric": "items_needing_review", "value": int(items.get("needs_review", pd.Series(dtype=bool)).fillna(False).sum()) if not items.empty else 0},
        ]
    )
    unmatched_simplifi = transactions[~transactions["transaction_id"].isin(matched_txn_ids)].copy() if not transactions.empty else pd.DataFrame()
    unmatched_orders = detail[detail["matched_simplifi_transaction_id"] == ""].copy() if not detail.empty else pd.DataFrame()
    review = items[items["needs_review"].fillna(False)].copy() if not items.empty else pd.DataFrame()
    return {
        "items": items,
        "reconciliation_summary": summary,
        "reconciliation_detail": detail,
        "unmatched_simplifi_transactions": unmatched_simplifi,
        "unmatched_retail_orders": unmatched_orders,
        "items_needing_review": review,
    }


def _orders(items: pd.DataFrame) -> pd.DataFrame:
    if items.empty:
        return pd.DataFrame()
    rows = []
    for (retailer, order_id), group in items.groupby(["retailer", "order_id"], dropna=False):
        rows.append(
            {
                "retailer": retailer,
                "order_id": order_id,
                "transaction_date": pd.to_datetime(group["transaction_date"].iloc[0]).date(),
                "merchant_normalized": group["merchant_normalized"].iloc[0],
                "calculated_total": sum((_dec(v) for v in group["allocated_total"]), Decimal("0.00")).quantize(TWOPLACES),
                "source_grand_total": first_decimal(group, "source_grand_total"),
            }
        )
    return pd.DataFrame(rows)


def _match_transaction(
    transactions: pd.DataFrame,
    order: pd.Series,
    tolerance: Decimal,
    date_window_days: int,
    already_matched: set[str] | None = None,
) -> str:
    order_date = pd.to_datetime(order["transaction_date"])
    retailer = normalize_merchant(order.get("retailer", ""))
    amount = abs(_dec(order["calculated_total"]))
    candidates = transactions.copy()
    candidates["_date"] = pd.to_datetime(candidates["posted_date"])
    candidates["_amount_abs"] = candidates["amount"].apply(lambda value: abs(_dec(value)))
    candidates = candidates[
        (abs((candidates["_date"] - order_date).dt.days) <= date_window_days)
        & (abs(candidates["_amount_abs"] - amount) <= tolerance)
        & (candidates["merchant_normalized"].astype(str).str.contains(retailer, case=False, na=False))
    ]
    if already_matched:
        candidates = candidates[~candidates["transaction_id"].isin(already_matched)]
    if candidates.empty:
        return ""
    return str(candidates.sort_values("_date").iloc[0]["transaction_id"])
