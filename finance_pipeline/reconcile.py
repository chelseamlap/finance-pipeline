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
    items = _reset_reconciliation_review_state(items)
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
        mismatch_diagnostic, mismatch_basis = _diagnose_total_mismatch(order, diff, amount_tolerance) if status == "total_mismatch" else ("", "")

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
            reason = status
            if mismatch_diagnostic:
                reason = f"{reason}: {mismatch_diagnostic}"
            items.loc[mask, "review_reason"] = items.loc[mask, "review_reason"].apply(lambda value: append_reason(value, reason))

        detail_rows.append(
            {
                "retailer": order["retailer"],
                "order_id": order["order_id"],
                "transaction_date": order["transaction_date"],
                "calculated_total": calc,
                "source_grand_total": source_total,
                "difference": diff,
                "item_subtotal_total": order.get("item_subtotal_total"),
                "item_discount_total": order.get("item_discount_total"),
                "allocated_tax_total": order.get("allocated_tax_total"),
                "allocated_shipping_total": order.get("allocated_shipping_total"),
                "allocated_fee_total": order.get("allocated_fee_total"),
                "source_order_total": order.get("source_order_total"),
                "source_tax_total": order.get("source_tax_total"),
                "source_discount_total": order.get("source_discount_total"),
                "source_shipping_total": order.get("source_shipping_total"),
                "source_fee_total": order.get("source_fee_total"),
                "item_rows": order.get("item_rows"),
                "matched_simplifi_transaction_id": match_id,
                "status": status,
                "mismatch_diagnostic": mismatch_diagnostic,
                "mismatch_basis": mismatch_basis,
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


def _reset_reconciliation_review_state(items: pd.DataFrame) -> pd.DataFrame:
    if items.empty:
        return items
    out = items.copy()
    if "review_reason" not in out.columns:
        out["review_reason"] = ""
    if "needs_review" not in out.columns:
        out["needs_review"] = False
    out["review_reason"] = out["review_reason"].apply(_strip_reconciliation_reasons)
    category_needs_review = out.get("household_category", pd.Series("", index=out.index)).fillna("").eq("Unknown_Review")
    has_reason = out["review_reason"].fillna("").astype(str).str.strip() != ""
    out["needs_review"] = out["needs_review"].fillna(False) & (has_reason | category_needs_review)
    return out


def _strip_reconciliation_reasons(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    stale = {"total_mismatch", "unmatched_transaction"}
    parts = [part.strip() for part in text.split(";")]
    return "; ".join(part for part in parts if part and part not in stale and not part.startswith("total_mismatch:"))


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
                "item_subtotal_total": _sum_column(group, "item_subtotal"),
                "item_discount_total": _sum_column(group, "item_discount"),
                "allocated_tax_total": _sum_column(group, "allocated_tax"),
                "allocated_shipping_total": _sum_column(group, "allocated_shipping"),
                "allocated_fee_total": _sum_column(group, "allocated_fee"),
                "source_order_total": first_decimal(group, "source_order_total"),
                "source_tax_total": first_decimal(group, "source_tax_total"),
                "source_discount_total": first_decimal(group, "source_discount_total"),
                "source_shipping_total": first_decimal(group, "source_shipping_total"),
                "source_fee_total": first_decimal(group, "source_fee_total"),
                "source_grand_total": first_decimal(group, "source_grand_total"),
                "item_rows": len(group),
            }
        )
    return pd.DataFrame(rows)


def _sum_column(group: pd.DataFrame, column: str) -> Decimal:
    if column not in group.columns:
        return Decimal("0.00")
    return sum((_dec(value) for value in group[column]), Decimal("0.00")).quantize(TWOPLACES)


def _diagnose_total_mismatch(order: pd.Series, difference: Decimal, tolerance: Decimal) -> tuple[str, str]:
    components = {
        "tax": _dec(order.get("allocated_tax_total")),
        "shipping": _dec(order.get("allocated_shipping_total")),
        "fee": _dec(order.get("allocated_fee_total")),
        "discount": _dec(order.get("item_discount_total")),
    }
    source_components = {
        "tax": order.get("source_tax_total"),
        "shipping": order.get("source_shipping_total"),
        "fee": order.get("source_fee_total"),
        "discount": order.get("source_discount_total"),
    }
    component_mismatches = []
    for name, source_value in source_components.items():
        if source_value is None or pd.isna(source_value):
            continue
        allocated = components[name]
        source = abs(_dec(source_value)) if name == "discount" else _dec(source_value)
        if abs(allocated - source) > tolerance:
            component_mismatches.append(f"{name}_component_mismatch")

    basis = "; ".join(
        [
            f"calculated_total={_dec(order.get('calculated_total'))}",
            f"source_grand_total={_dec(order.get('source_grand_total'))}",
            f"difference={difference}",
            f"item_subtotal_total={_dec(order.get('item_subtotal_total'))}",
            f"item_discount_total={_dec(order.get('item_discount_total'))}",
            f"allocated_tax_total={components['tax']}",
            f"allocated_shipping_total={components['shipping']}",
            f"allocated_fee_total={components['fee']}",
            f"item_rows={int(order.get('item_rows') or 0)}",
        ]
    )

    if component_mismatches:
        return "+".join(component_mismatches), basis
    for name in ["shipping", "tax", "fee"]:
        amount = components[name]
        if amount and abs(abs(difference) - abs(amount)) <= tolerance:
            direction = "included_in_items_not_charge" if difference > 0 else "missing_from_items"
            return f"{name}_{direction}", basis
    discount = components["discount"]
    if discount and abs(abs(difference) - abs(discount)) <= tolerance:
        direction = "over_applied" if difference < 0 else "missing_or_under_applied"
        return f"discount_{direction}", basis
    if int(order.get("item_rows") or 0) == 1:
        return "single_item_price_or_adjustment_mismatch", basis
    if difference > 0:
        return "source_total_lower_than_item_components", basis
    return "source_total_higher_than_item_components", basis


def _match_transaction(
    transactions: pd.DataFrame,
    order: pd.Series,
    tolerance: Decimal,
    date_window_days: int,
    already_matched: set[str] | None = None,
) -> str:
    order_date = pd.to_datetime(order["transaction_date"])
    retailer = normalize_merchant(order.get("retailer", ""))
    match_total = order.get("source_grand_total")
    amount = abs(_dec(match_total) if pd.notna(match_total) and str(match_total) != "" else _dec(order["calculated_total"]))
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
