from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations

import pandas as pd

from .categorize import append_reason
from .models import TWOPLACES, money
from .normalize import normalize_merchant


@dataclass(frozen=True)
class CostComponent:
    label: str
    source_column: str
    allocation_column: str
    sign: int = 1
    use_absolute_value: bool = False


ORDER_COST_COMPONENTS = (
    CostComponent("tax", "source_tax_total", "allocated_tax"),
    CostComponent("shipping", "source_shipping_total", "allocated_shipping"),
    CostComponent("fee", "source_fee_total", "allocated_fee"),
    CostComponent("discount", "source_discount_total", "item_discount", sign=-1, use_absolute_value=True),
)


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
        out[col] = out[col].astype("object")
    if "component_allocation_notes" not in out.columns:
        out["component_allocation_notes"] = ""
    out = _normalize_discount_amounts(out)

    group_keys = ["retailer", "order_id"]
    for keys, group in out.groupby(group_keys, dropna=False):
        indices = list(group.index)
        positive_subtotals = [_dec(out.at[i, "item_subtotal"]) if _dec(out.at[i, "item_subtotal"]) > 0 else Decimal("0") for i in indices]
        base = sum(positive_subtotals, Decimal("0"))
        consistency_base = sum((_dec(out.at[i, "item_subtotal"]) for i in indices), Decimal("0")).quantize(TWOPLACES)
        effective_components, notes = _consistent_source_components(group, consistency_base, tolerance)

        for component in ORDER_COST_COMPONENTS:
            source_total = effective_components.get(component.source_column)
            original_source_total = first_decimal(group, component.source_column)
            current_total = sum((_dec(out.at[i, component.allocation_column]) for i in indices), Decimal("0"))
            if source_total is None:
                continue
            component_amount = _component_amount(component, source_total)
            if component_amount == 0 and original_source_total not in {None, Decimal("0.00")}:
                _allocate(out, indices, positive_subtotals, base, Decimal("0.00"), component.allocation_column)
            elif component_amount != 0 and (abs(current_total) <= tolerance or abs(current_total - component_amount) > tolerance):
                _allocate(out, indices, positive_subtotals, base, component_amount, component.allocation_column)

        if notes:
            note = "; ".join(notes)
            for idx in indices:
                out.at[idx, "component_allocation_notes"] = append_reason(out.at[idx, "component_allocation_notes"], note)

    out["allocated_total"] = out.apply(
        lambda row: (_dec(row.get("item_subtotal")) - _dec(row.get("item_discount")) + _dec(row.get("allocated_tax")) + _dec(row.get("allocated_shipping")) + _dec(row.get("allocated_fee"))).quantize(TWOPLACES),
        axis=1,
    )
    return out


def _normalize_discount_amounts(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in ["item_discount", "source_discount_total"]:
        if column not in out.columns:
            continue
        out[column] = out[column].astype("object")
        for idx, value in out[column].items():
            amount = _dec(value)
            if amount < 0:
                out.at[idx, column] = abs(amount).quantize(TWOPLACES)
                out.at[idx, "component_allocation_notes"] = append_reason(
                    out.at[idx, "component_allocation_notes"],
                    f"{column}_normalized_to_positive_amount_to_subtract",
                )
    return out


def _consistent_source_components(group: pd.DataFrame, base: Decimal, tolerance: Decimal) -> tuple[dict[str, Decimal | None], list[str]]:
    source_grand = first_decimal(group, "source_grand_total")
    source_values = _source_component_values(group)
    if source_grand is None:
        return source_values, []

    present = _present_components(source_values)
    if not present:
        return source_values, []

    if abs(_component_total(base, present, source_values) - source_grand) <= tolerance:
        return source_values, []

    matches = _matching_component_subsets(base, source_grand, present, source_values, tolerance)
    if not matches:
        return source_values, []

    selected = sorted(matches, key=lambda match: (len(match), _component_amount_sum(match, source_values)), reverse=True)[0]
    effective = source_values.copy()
    notes: list[str] = []
    for component in present:
        if component in selected:
            continue
        effective[component.source_column] = Decimal("0.00")
        notes.append(f"{component.source_column}_excluded_from_allocated_total_to_match_source_grand_total")
    return effective, notes


def _source_component_values(group: pd.DataFrame) -> dict[str, Decimal | None]:
    return {component.source_column: first_decimal(group, component.source_column) for component in ORDER_COST_COMPONENTS}


def _present_components(source_values: dict[str, Decimal | None]) -> list[CostComponent]:
    return [
        component
        for component in ORDER_COST_COMPONENTS
        if source_values.get(component.source_column) is not None
        and _component_amount(component, source_values[component.source_column] or Decimal("0.00")) != 0
    ]


def _matching_component_subsets(
    base: Decimal,
    source_grand: Decimal,
    components: list[CostComponent],
    source_values: dict[str, Decimal | None],
    tolerance: Decimal,
) -> list[tuple[CostComponent, ...]]:
    matches: list[tuple[CostComponent, ...]] = []
    for size in range(len(components) + 1):
        for subset in combinations(components, size):
            if abs(_component_total(base, subset, source_values) - source_grand) <= tolerance:
                matches.append(subset)
    return matches


def _component_total(base: Decimal, components: list[CostComponent] | tuple[CostComponent, ...], source_values: dict[str, Decimal | None]) -> Decimal:
    total = base
    for component in components:
        source_value = source_values.get(component.source_column)
        if source_value is None:
            continue
        total += component.sign * _component_amount(component, source_value)
    return total.quantize(TWOPLACES)


def _component_amount_sum(components: tuple[CostComponent, ...], source_values: dict[str, Decimal | None]) -> Decimal:
    return sum((_component_amount(component, source_values[component.source_column] or Decimal("0.00")) for component in components), Decimal("0.00"))


def _component_amount(component: CostComponent, value: Decimal) -> Decimal:
    amount = abs(value) if component.use_absolute_value else value
    return amount.quantize(TWOPLACES)


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
        item_derived_total = _dec(order["item_derived_total"])
        retailer_source_grand = order.get("retailer_source_grand_total")
        retailer_source_total = _dec(retailer_source_grand) if pd.notna(retailer_source_grand) and str(retailer_source_grand) != "" else None
        item_vs_retailer_difference = (item_derived_total - retailer_source_total).quantize(TWOPLACES) if retailer_source_total is not None else Decimal("0.00")
        status = "ok" if retailer_source_total is None or abs(item_vs_retailer_difference) <= amount_tolerance else "total_mismatch"
        base_difference = _base_difference_after_components(order, retailer_source_total) if retailer_source_total is not None else Decimal("0.00")
        mismatch_diagnostic, mismatch_basis = _diagnose_total_mismatch(order, item_vs_retailer_difference, base_difference, amount_tolerance) if status == "total_mismatch" else ("", "")

        match_id = ""
        if not transactions.empty:
            match_id = _match_transaction(transactions, order, amount_tolerance, date_window_days, matched_txn_ids)
        simplifi_amount = _matched_simplifi_amount(transactions, match_id) if match_id else None
        simplifi_reconciled_total = (-simplifi_amount).quantize(TWOPLACES) if simplifi_amount is not None else None
        item_vs_simplifi_difference = (item_derived_total - simplifi_reconciled_total).quantize(TWOPLACES) if simplifi_reconciled_total is not None else None
        retailer_vs_simplifi_difference = (retailer_source_total - simplifi_reconciled_total).quantize(TWOPLACES) if retailer_source_total is not None and simplifi_reconciled_total is not None else None

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
                "simplifi_amount": simplifi_amount,
                "simplifi_reconciled_total": simplifi_reconciled_total,
                "item_derived_total": item_derived_total,
                "retailer_source_grand_total": retailer_source_total,
                "item_vs_simplifi_difference": item_vs_simplifi_difference,
                "retailer_vs_simplifi_difference": retailer_vs_simplifi_difference,
                "item_vs_retailer_difference": item_vs_retailer_difference,
                "base_difference_after_components": base_difference,
                "item_subtotal_total": order.get("item_subtotal_total"),
                "item_discount_total": order.get("item_discount_total"),
                "allocated_tax_total": order.get("allocated_tax_total"),
                "allocated_shipping_total": order.get("allocated_shipping_total"),
                "allocated_fee_total": order.get("allocated_fee_total"),
                "retailer_source_order_total": order.get("retailer_source_order_total"),
                "retailer_source_tax_total": order.get("retailer_source_tax_total"),
                "retailer_source_discount_total": order.get("retailer_source_discount_total"),
                "retailer_source_shipping_total": order.get("retailer_source_shipping_total"),
                "retailer_source_fee_total": order.get("retailer_source_fee_total"),
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
                "item_derived_total": sum((_dec(v) for v in group["allocated_total"]), Decimal("0.00")).quantize(TWOPLACES),
                "item_subtotal_total": _sum_column(group, "item_subtotal"),
                "item_discount_total": _sum_column(group, "item_discount"),
                "allocated_tax_total": _sum_column(group, "allocated_tax"),
                "allocated_shipping_total": _sum_column(group, "allocated_shipping"),
                "allocated_fee_total": _sum_column(group, "allocated_fee"),
                "retailer_source_order_total": first_decimal(group, "source_order_total"),
                "retailer_source_tax_total": first_decimal(group, "source_tax_total"),
                "retailer_source_discount_total": first_decimal(group, "source_discount_total"),
                "retailer_source_shipping_total": first_decimal(group, "source_shipping_total"),
                "retailer_source_fee_total": first_decimal(group, "source_fee_total"),
                "retailer_source_grand_total": first_decimal(group, "source_grand_total"),
                "item_rows": len(group),
            }
        )
    return pd.DataFrame(rows)


def _sum_column(group: pd.DataFrame, column: str) -> Decimal:
    if column not in group.columns:
        return Decimal("0.00")
    return sum((_dec(value) for value in group[column]), Decimal("0.00")).quantize(TWOPLACES)


def _diagnose_total_mismatch(order: pd.Series, difference: Decimal, base_difference: Decimal, tolerance: Decimal) -> tuple[str, str]:
    components = {
        "tax": _dec(order.get("allocated_tax_total")),
        "shipping": _dec(order.get("allocated_shipping_total")),
        "fee": _dec(order.get("allocated_fee_total")),
        "discount": _dec(order.get("item_discount_total")),
    }
    source_components = {
        "tax": order.get("retailer_source_tax_total"),
        "shipping": order.get("retailer_source_shipping_total"),
        "fee": order.get("retailer_source_fee_total"),
        "discount": order.get("retailer_source_discount_total"),
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
            f"item_derived_total={_dec(order.get('item_derived_total'))}",
            f"retailer_source_grand_total={_dec(order.get('retailer_source_grand_total'))}",
            f"item_vs_retailer_difference={difference}",
            f"item_subtotal_total={_dec(order.get('item_subtotal_total'))}",
            f"expected_item_subtotal_after_components={(_dec(order.get('item_subtotal_total')) - base_difference).quantize(TWOPLACES)}",
            f"base_difference_after_components={base_difference}",
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
    if abs(base_difference) > tolerance:
        if int(order.get("item_rows") or 0) == 1:
            direction = "higher" if base_difference > 0 else "lower"
            return f"single_item_base_{direction}_than_source_after_components", basis
        direction = "higher" if base_difference > 0 else "lower"
        return f"item_base_total_{direction}_than_source_after_components", basis
    if int(order.get("item_rows") or 0) == 1:
        return "single_item_price_or_adjustment_mismatch", basis
    if difference > 0:
        return "retailer_source_total_lower_than_item_components", basis
    return "retailer_source_total_higher_than_item_components", basis


def _base_difference_after_components(order: pd.Series, retailer_source_total: Decimal) -> Decimal:
    expected_base = (
        retailer_source_total
        + _dec(order.get("item_discount_total"))
        - _dec(order.get("allocated_tax_total"))
        - _dec(order.get("allocated_shipping_total"))
        - _dec(order.get("allocated_fee_total"))
    ).quantize(TWOPLACES)
    return (_dec(order.get("item_subtotal_total")) - expected_base).quantize(TWOPLACES)


def _matched_simplifi_amount(transactions: pd.DataFrame, transaction_id: str) -> Decimal | None:
    if transactions.empty or not transaction_id:
        return None
    matches = transactions[transactions["transaction_id"] == transaction_id]
    if matches.empty:
        return None
    value = matches.iloc[0].get("amount")
    if value is None or str(value).strip() in {"", "nan", "NaN", "None"}:
        return None
    return _dec(value)


def _match_transaction(
    transactions: pd.DataFrame,
    order: pd.Series,
    tolerance: Decimal,
    date_window_days: int,
    already_matched: set[str] | None = None,
) -> str:
    order_date = pd.to_datetime(order["transaction_date"])
    retailer = normalize_merchant(order.get("retailer", ""))
    match_total = order.get("retailer_source_grand_total")
    amount = _dec(match_total) if pd.notna(match_total) and str(match_total) != "" else _dec(order["item_derived_total"])
    candidates = transactions.copy()
    candidates["_date"] = pd.to_datetime(candidates["posted_date"])
    candidates["_simplifi_reconciled_total"] = candidates["amount"].apply(lambda value: (-_dec(value)).quantize(TWOPLACES))
    candidates = candidates[
        (abs((candidates["_date"] - order_date).dt.days) <= date_window_days)
        & (abs(candidates["_simplifi_reconciled_total"] - amount) <= tolerance)
        & (candidates["merchant_normalized"].astype(str).str.contains(retailer, case=False, na=False))
    ]
    if already_matched:
        candidates = candidates[~candidates["transaction_id"].isin(already_matched)]
    if candidates.empty:
        return ""
    return str(candidates.sort_values("_date").iloc[0]["transaction_id"])
