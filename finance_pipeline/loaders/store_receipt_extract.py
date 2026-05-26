from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path

import pandas as pd

from finance_pipeline.identity import DuplicateOrdinalTracker, infer_source_owner, retail_identity_parts, row_fingerprint, stable_hash
from finance_pipeline.loaders.generic import reject_rows, source_files
from finance_pipeline.loaders.retail_common import derive_line_subtotal
from finance_pipeline.models import CanonicalRetailItem, money
from finance_pipeline.normalize import clean_string, normalize_merchant, normalize_text, parse_date
from finance_pipeline.reconcile import allocate_order_amounts

LOGGER = logging.getLogger(__name__)

SOURCE_ADAPTER = "store_receipt_extract"
ORDER_COLUMNS = {"retailer", "order_id", "ordered_at", "total"}
ITEM_COLUMNS = {"retailer", "order_id", "line_index", "name"}


def load(path: Path, import_batch_id: str, store: str | None = None):
    files = source_files(path)
    csv_rows = _load_csv_exports(files, import_batch_id, store)
    json_rows = _load_json_exports(files, import_batch_id, store)
    df = pd.DataFrame([*csv_rows, *json_rows])
    if not df.empty:
        df = df.drop_duplicates("item_id", keep="last")
        df = allocate_order_amounts(df)
    return df


def _load_csv_exports(files: list[Path], import_batch_id: str, store: str | None) -> list[dict]:
    csv_files = [file for file in files if file.suffix.lower() == ".csv"]
    if not csv_files:
        return []
    orders = []
    items = []
    for file in csv_files:
        try:
            df = pd.read_csv(file, dtype=str, keep_default_na=False)
        except Exception as exc:
            reject_rows([], file, f"CSV read error: {exc}", Path("data/rejected"))
            continue
        columns = set(df.columns)
        df["_export_file"] = str(file)
        df["_source_owner"] = infer_source_owner(file)
        if ORDER_COLUMNS.issubset(columns):
            orders.append(df)
        elif ITEM_COLUMNS.issubset(columns):
            items.append(df)
        else:
            LOGGER.warning("store_receipt_extract file %s is not an orders or order_items CSV", file)
            reject_rows(df.to_dict("records"), file, "unrecognized store_receipt_extract CSV schema", Path("data/rejected"))
    if not orders and not items:
        return []
    if not orders or not items:
        missing = "orders" if not orders else "order_items"
        LOGGER.warning("store_receipt_extract CSV export is missing %s file(s)", missing)

    orders_df = _dedupe_orders(pd.concat(orders, ignore_index=True) if orders else pd.DataFrame())
    items_df = _dedupe_items(pd.concat(items, ignore_index=True) if items else pd.DataFrame())
    if store:
        orders_df = orders_df[orders_df["retailer"].astype(str).str.lower().eq(store.lower())]
        items_df = items_df[items_df["retailer"].astype(str).str.lower().eq(store.lower())]
    merged = items_df.merge(
        orders_df.drop(columns=["_export_file", "_source_owner"], errors="ignore"),
        on=["retailer", "order_id"],
        how="left",
        suffixes=("", "_order"),
    )
    merged = _fill_unpriced_order_items(merged)
    return _canonical_rows(merged.to_dict("records"), import_batch_id)


def _load_json_exports(files: list[Path], import_batch_id: str, store: str | None) -> list[dict]:
    rows = []
    for file in [file for file in files if file.suffix.lower() == ".json"]:
        try:
            with file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            reject_rows([], file, f"JSON read error: {exc}", Path("data/rejected"))
            continue
        orders = payload.get("orders", []) if isinstance(payload, dict) else []
        if not isinstance(orders, list):
            reject_rows([payload if isinstance(payload, dict) else {"payload": payload}], file, "JSON export missing orders list", Path("data/rejected"))
            continue
        for order in orders:
            if store and str(order.get("retailer", "")).lower() != store.lower():
                continue
            for item in order.get("items") or []:
                rows.append(_json_item_row(order, item, file))
    return _canonical_rows(rows, import_batch_id)


def _dedupe_orders(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.sort_values("_export_file").drop_duplicates(["retailer", "order_id"], keep="last")


def _dedupe_items(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.sort_values("_export_file").drop_duplicates(["retailer", "order_id", "line_index"], keep="last")


def _fill_unpriced_order_items(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "line_total" not in df.columns:
        return df
    out = df.copy()
    out["_allocation_note"] = ""
    for keys, group in out.groupby(["retailer", "order_id"], dropna=False):
        line_totals = group["line_total"].fillna("").astype(str).str.strip()
        if not line_totals.eq("").all():
            continue
        total = _optional_money(group["subtotal"].iloc[0] if "subtotal" in group.columns else "")
        if total is None:
            total = _optional_money(group["total"].iloc[0] if "total" in group.columns else "")
        if total is None or len(group) == 0:
            continue
        shares = _even_shares(total, len(group))
        for idx, share in zip(group.index, shares):
            out.at[idx, "line_total"] = str(share)
            if not str(out.at[idx, "unit_price"]).strip():
                quantity = _optional_money(out.at[idx, "quantity"]) or Decimal("1.00")
                out.at[idx, "unit_price"] = str((share / quantity).quantize(Decimal("0.01"))) if quantity else str(share)
            out.at[idx, "_allocation_note"] = "missing_line_total_evenly_allocated_from_order_total"
    return out


def _even_shares(total: Decimal, count: int) -> list[Decimal]:
    if count <= 0:
        return []
    base = (total / count).quantize(Decimal("0.01"))
    shares = [base for _ in range(count)]
    shares[-1] = (total - sum(shares[:-1], Decimal("0.00"))).quantize(Decimal("0.01"))
    return shares


def _json_item_row(order: dict, item: dict, file: Path) -> dict:
    return {
        "retailer": order.get("retailer"),
        "order_id": order.get("order_id"),
        "ordered_at": order.get("ordered_at"),
        "total": order.get("total"),
        "subtotal": order.get("subtotal"),
        "tax": order.get("tax"),
        "shipping": order.get("shipping"),
        "account_hint": order.get("account_hint"),
        "line_index": item.get("line_index"),
        "sku": item.get("sku"),
        "name": item.get("name"),
        "quantity": item.get("quantity"),
        "unit_price": item.get("unit_price"),
        "line_total": item.get("line_total"),
        "category_native": item.get("category_native"),
        "dpci": item.get("dpci"),
        "_export_file": str(file),
        "_source_owner": infer_source_owner(file),
    }


def _canonical_rows(rows: list[dict], import_batch_id: str) -> list[dict]:
    canonical = []
    rejected = []
    ordinals = DuplicateOrdinalTracker()
    for row in rows:
        try:
            canonical.append(_canonical_item(row, import_batch_id, ordinals))
        except Exception as exc:
            rejected.append({**row, "reject_reason": str(exc)})
    if rejected:
        reject_rows(rejected, Path("store_receipt_extract"), "row parse error", Path("data/rejected"))
    return canonical


def _canonical_item(row: dict, import_batch_id: str, ordinals: DuplicateOrdinalTracker) -> dict:
    retailer = clean_string(row.get("retailer")).lower()
    if retailer not in {"target", "costco"}:
        raise ValueError(f"unsupported retailer: {retailer}")
    fallback_reasons = []
    description = clean_string(row.get("name"))
    if not description:
        description = _fallback_description(row)
        fallback_reasons.append("missing item name")
    quantity = money(row.get("quantity"), Decimal("1")) if clean_string(row.get("quantity")) else Decimal("1")
    unit_price = money(row.get("unit_price")) if clean_string(row.get("unit_price")) else Decimal("0")
    data = {"item_subtotal": row.get("line_total"), "quantity": quantity, "unit_price": unit_price}
    raw_subtotal, subtotal, subtotal_note = derive_line_subtotal(data, quantity, unit_price)
    source_owner = clean_string(row.get("_source_owner")) or _source_owner_from_account_hint(row.get("account_hint"))
    base = {
        "source_adapter": SOURCE_ADAPTER,
        "retailer": retailer,
        "source_owner": source_owner,
        "order_id": clean_string(row.get("order_id")),
        "receipt_id": "",
        "transaction_date": parse_date(row.get("ordered_at")),
        "merchant_raw": retailer,
        "merchant_normalized": normalize_merchant(retailer),
        "item_description_raw": description,
        "item_description_normalized": normalize_text(description),
        "sku": clean_string(row.get("sku")),
        "asin": "",
        "upc": "",
        "quantity": quantity,
        "unit_price": unit_price,
        "item_subtotal_raw": raw_subtotal,
        "line_subtotal_derived": subtotal,
        "item_subtotal": subtotal,
        "item_discount": Decimal("0.00"),
        "allocated_tax": Decimal("0.00"),
        "allocated_shipping": Decimal("0.00"),
        "allocated_fee": Decimal("0.00"),
        "allocated_total": subtotal,
        "item_subtotal_derivation_notes": subtotal_note,
        "dedupe_notes": clean_string(row.get("_allocation_note")),
        "source_order_total": _optional_money(row.get("subtotal")),
        "source_tax_total": _optional_money(row.get("tax")),
        "source_discount_total": None,
        "source_shipping_total": _optional_money(row.get("shipping")),
        "source_fee_total": None,
        "source_grand_total": _optional_money(row.get("total")),
        "file_source": clean_string(row.get("_export_file")),
        "import_batch_id": import_batch_id,
        "source_category_raw": _source_category_raw(row),
    }
    review_reasons = [*fallback_reasons]
    if clean_string(row.get("_allocation_note")):
        review_reasons.append(clean_string(row.get("_allocation_note")))
    if review_reasons:
        base["needs_review"] = True
        base["review_reason"] = "; ".join(review_reasons)
    identity_parts = retail_identity_parts(base)
    ordinal = ordinals.next([*identity_parts, clean_string(row.get("line_index"))])
    item = CanonicalRetailItem(
        item_id=stable_hash([*identity_parts, clean_string(row.get("line_index")), ordinal]),
        row_fingerprint=row_fingerprint(base, base.keys()),
        **base,
    )
    return item.model_dump()


def _optional_money(value: object):
    text = clean_string(value)
    return money(text) if text else None


def _source_owner_from_account_hint(value: object) -> str:
    hint = clean_string(value)
    if not hint:
        return ""
    return normalize_text(hint.split("@", 1)[0])


def _fallback_description(row: dict) -> str:
    retailer = clean_string(row.get("retailer")) or "store"
    sku = clean_string(row.get("sku")) or "unknown-sku"
    category = _source_category_raw(row)
    suffix = f" ({category})" if category else ""
    return f"{retailer.title()} item {sku}{suffix}"


def _source_category_raw(row: dict) -> str:
    label = clean_string(row.get("category_label"))
    native = clean_string(row.get("category_native"))
    if label and native and label != native:
        return f"{native}: {label}"
    return label or native
