from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import pandas as pd


@dataclass(frozen=True)
class DuplicateItemGroup:
    indices: list[int]
    value_cents: int


DUPLICATE_ITEM_KEY_COLUMNS = [
    "retailer",
    "order_id",
    "source_adapter",
    "transaction_date",
    "item_description_normalized",
    "sku",
    "asin",
    "upc",
    "quantity",
    "unit_price",
    "item_subtotal",
    "item_discount",
    "source_order_total",
    "source_tax_total",
    "source_discount_total",
    "source_shipping_total",
    "source_fee_total",
    "source_grand_total",
]


SOURCE_PRIORITY = {
    "amazon": {
        "amazon_order_history_reporter": 100,
        "amazon_order_history_exporter": 80,
        "orderpro": 20,
    },
    "target": {
        "orderpro": 100,
        "target_manual": 50,
    },
    "costco": {
        "orderpro": 100,
        "costco_receipt_downloader": 80,
    },
}


def dedupe_retail_items(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not {"retailer", "order_id", "source_adapter"}.issubset(df.columns):
        return df
    out = df.copy()
    if "dedupe_notes" not in out.columns:
        out["dedupe_notes"] = ""

    out = _collapse_same_order_duplicate_items(out)

    keep_indices: list[int] = []
    for (_, _), group in out.groupby(["retailer", "order_id"], dropna=False, sort=False):
        adapters = sorted(set(group["source_adapter"].fillna("").astype(str)))
        if len(adapters) <= 1:
            keep_indices.extend(group.index.tolist())
            continue
        selected_adapter = _preferred_adapter(group)
        selected = group[group["source_adapter"].astype(str) == selected_adapter]
        dropped = [adapter for adapter in adapters if adapter != selected_adapter]
        note = f"deduped_order_sources: kept {selected_adapter}; dropped {','.join(dropped)}"
        out.loc[selected.index, "dedupe_notes"] = out.loc[selected.index, "dedupe_notes"].apply(lambda value: _append_note(value, note))
        keep_indices.extend(selected.index.tolist())

    return out.loc[sorted(keep_indices)].reset_index(drop=True)


def _collapse_same_order_duplicate_items(df: pd.DataFrame) -> pd.DataFrame:
    existing_keys = [column for column in DUPLICATE_ITEM_KEY_COLUMNS if column in df.columns]
    if not {"retailer", "order_id", "source_adapter"}.issubset(existing_keys):
        return df

    orderpro_mask = df["source_adapter"].astype(str).eq("orderpro")
    if not orderpro_mask.any():
        return df

    out = df.copy()
    for money_column in ["item_subtotal", "line_subtotal_derived", "allocated_total"]:
        if money_column in out.columns:
            out[money_column] = out[money_column].astype("object")
    keep_indices: list[int] = out.index[~orderpro_mask].tolist()
    duplicate_notes: dict[int, str] = {}

    orderpro = out.loc[orderpro_mask]
    for _, order_group in orderpro.groupby(["retailer", "order_id", "source_adapter"], dropna=False, sort=False):
        selected, notes, subtotal_overrides = _select_duplicate_multiplicity(order_group, existing_keys)
        keep_indices.extend(selected)
        duplicate_notes.update(notes)
        for idx, subtotal in subtotal_overrides.items():
            out.at[idx, "item_subtotal"] = subtotal
            if "line_subtotal_derived" in out.columns:
                out.at[idx, "line_subtotal_derived"] = subtotal
            if "item_subtotal_derivation_notes" in out.columns:
                out.at[idx, "item_subtotal_derivation_notes"] = _append_note(
                    _strip_note(out.at[idx, "item_subtotal_derivation_notes"], "item_subtotal_derived_from_quantity_times_unit_price"),
                    "item_subtotal_derived_from_source_order_total",
                )
            if "allocated_total" in out.columns:
                out.at[idx, "allocated_total"] = subtotal

    out = out.loc[sorted(keep_indices)].copy()
    for idx, note in duplicate_notes.items():
        out.at[idx, "dedupe_notes"] = _append_note(out.at[idx, "dedupe_notes"], note)
    return out.reset_index(drop=True)


def _select_duplicate_multiplicity(order_group: pd.DataFrame, key_columns: list[str]) -> tuple[list[int], dict[int, str], dict[int, Decimal]]:
    normalized_keys = order_group[key_columns].copy()
    for column in key_columns:
        normalized_keys[column] = normalized_keys[column].map(_dedupe_key_value)
    normalized_keys["_original_index"] = order_group.index

    item_groups: list[DuplicateItemGroup] = []
    for _, key_group in normalized_keys.groupby(key_columns, dropna=False, sort=False):
        indices = key_group["_original_index"].tolist()
        value_cents = _row_item_value_cents(order_group.loc[indices[0]])
        item_groups.append(DuplicateItemGroup(indices=indices, value_cents=value_cents))

    target_cents = _source_order_target_cents(order_group)
    single_item_override = _single_item_source_total_override(item_groups, target_cents)
    if single_item_override is not None:
        selected_idx = item_groups[0].indices[0]
        collapsed = len(item_groups[0].indices) - 1
        note = (
            f"deduped_duplicate_item_rows: kept 1 of {len(item_groups[0].indices)} duplicate row(s); "
            f"collapsed {collapsed} duplicate row(s); item_subtotal set to source_order_total"
        )
        return [selected_idx], {selected_idx: note}, {selected_idx: _decimal_from_cents(single_item_override)}

    if all(len(group.indices) == 1 for group in item_groups):
        return order_group.index.tolist(), {}, {}

    selected_counts = _best_duplicate_counts(item_groups, target_cents)

    selected_indices: list[int] = []
    notes: dict[int, str] = {}
    for item_group, keep_count in zip(item_groups, selected_counts):
        selected_indices.extend(item_group.indices[:keep_count])
        duplicate_count = len(item_group.indices) - keep_count
        if duplicate_count > 0:
            target_note = " to reconcile source_order_total" if target_cents is not None else ""
            notes[item_group.indices[0]] = (
                f"deduped_duplicate_item_rows: kept {keep_count} of {len(item_group.indices)} "
                f"duplicate row(s); collapsed {duplicate_count} duplicate row(s){target_note}"
            )
    return selected_indices, notes, {}


def _single_item_source_total_override(item_groups: list[DuplicateItemGroup], target_cents: int | None) -> int | None:
    if target_cents is None or len(item_groups) != 1:
        return None
    group = item_groups[0]
    best_existing = min(abs((group.value_cents * keep_count) - target_cents) for keep_count in range(1, len(group.indices) + 1))
    if abs(target_cents) > 0 and best_existing > 3:
        return target_cents
    return None


def _decimal_from_cents(cents: int) -> Decimal:
    return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))


def _best_duplicate_counts(item_groups: list[DuplicateItemGroup], target_cents: int | None) -> list[int]:
    if target_cents is None:
        return [1 for _ in item_groups]

    states: dict[int, tuple[int, ...]] = {0: ()}
    for group in item_groups:
        next_states: dict[int, tuple[int, ...]] = {}
        for running_total, counts in states.items():
            for keep_count in range(1, len(group.indices) + 1):
                total = running_total + (group.value_cents * keep_count)
                candidate = counts + (keep_count,)
                existing = next_states.get(total)
                if existing is None or _count_tiebreak(candidate) < _count_tiebreak(existing):
                    next_states[total] = candidate
        states = next_states

    best_total, best_counts = min(
        states.items(),
        key=lambda item: (abs(item[0] - target_cents), _count_tiebreak(item[1])),
    )
    return list(best_counts)


def _count_tiebreak(counts: tuple[int, ...]) -> tuple[int, tuple[int, ...]]:
    # Prefer fewer retained duplicate rows if two combinations reconcile equally well.
    return (sum(counts), counts)


def _source_order_target_cents(order_group: pd.DataFrame) -> int | None:
    if "source_order_total" not in order_group.columns:
        return None
    for value in order_group["source_order_total"]:
        cents = _money_cents(value)
        if cents is not None:
            return cents
    return None


def _row_item_value_cents(row: pd.Series) -> int:
    for column in ["item_subtotal", "allocated_total", "unit_price"]:
        if column in row.index:
            cents = _money_cents(row.get(column))
            if cents is not None:
                return cents
    return 0


def _money_cents(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    if text.lower() in {"", "nan", "none", "nat"}:
        return None
    try:
        amount = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _dedupe_key_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return text.lower()


def _preferred_adapter(group: pd.DataFrame) -> str:
    retailer = str(group["retailer"].iloc[0])
    priority = SOURCE_PRIORITY.get(retailer, {})
    summary = (
        group.groupby("source_adapter", dropna=False)
        .agg(rows=("item_id", "count"), total=("allocated_total", "sum"))
        .reset_index()
    )
    summary["priority"] = summary["source_adapter"].astype(str).map(priority).fillna(0)
    summary["abs_total"] = summary["total"].abs()
    selected = summary.sort_values(["priority", "rows", "abs_total", "source_adapter"], ascending=[False, False, False, True]).iloc[0]
    return str(selected["source_adapter"])


def _append_note(existing: object, note: str) -> str:
    text = "" if existing is None else str(existing).strip()
    if not text or text.lower() == "nan":
        return note
    if note in text:
        return text
    return f"{text}; {note}"


def _strip_note(existing: object, note: str) -> str:
    text = "" if existing is None else str(existing).strip()
    if not text or text.lower() == "nan":
        return ""
    parts = [part.strip() for part in text.split(";")]
    return "; ".join(part for part in parts if part and part != note)
