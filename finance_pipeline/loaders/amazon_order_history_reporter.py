from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from finance_pipeline.identity import (
    DuplicateOrdinalTracker,
    infer_source_owner,
    retail_identity_parts,
    row_fingerprint,
    stable_hash,
)
from finance_pipeline.loaders.generic import apply_aliases, read_file, reject_rows, source_files, str_or_blank
from finance_pipeline.models import CanonicalRetailItem, money
from finance_pipeline.normalize import clean_string, normalize_merchant, normalize_text, parse_date
from finance_pipeline.reconcile import allocate_order_amounts

from .retail_common import load_retail_items

LOGGER = logging.getLogger(__name__)


def load(path: Path, import_batch_id: str, store: str | None = None):
    order_totals = _load_order_totals(path)
    item_level = _load_item_level_exports(path, import_batch_id, order_totals)
    if not item_level.empty:
        return item_level
    order_level = _load_order_level_exports(path, import_batch_id)
    if not order_level.empty:
        return order_level
    return load_retail_items(path, import_batch_id, source_adapter="amazon_order_history_reporter", retailer="amazon")


def _load_order_totals(path: Path) -> dict[str, dict]:
    orders: dict[str, dict] = {}
    for file in source_files(path):
        raw = read_file(file)
        normalized_cols = {str(col).strip().lower() for col in raw.columns}
        if not {"order id", "date", "total"}.issubset(normalized_cols):
            continue
        df = raw.rename(columns={col: str(col).strip().lower() for col in raw.columns})
        for _, raw_row in df.iterrows():
            data = raw_row.to_dict()
            order_id = str_or_blank(data, "order id")
            if not order_id or order_id.lower() == "order id":
                continue
            orders[order_id] = {
                "source_tax_total": money(data.get("tax", "0")),
                "source_shipping_total": money(data.get("shipping", "0")) - money(data.get("shipping_refund", "0")),
                "source_discount_total": money(data.get("refund", "0")) + money(data.get("gift", "0")),
                "source_grand_total": money(data.get("total")),
            }
    return orders


def _load_item_level_exports(path: Path, import_batch_id: str, order_totals: dict[str, dict]) -> pd.DataFrame:
    rows: list[dict] = []
    ordinals = DuplicateOrdinalTracker()
    rejected_dir = Path("data/rejected")
    for file in source_files(path):
        raw = read_file(file)
        normalized_cols = {str(col).strip().lower() for col in raw.columns}
        if not {"order id", "order date", "description", "price"}.issubset(normalized_cols):
            continue
        df = _normalize_item_columns(raw)
        for _, raw_row in df.iterrows():
            data = raw_row.to_dict()
            try:
                order_id = str_or_blank(data, "order_id")
                desc = str_or_blank(data, "item_description_raw")
                if order_id.lower() == "order id" or desc.lower() == "description":
                    continue
                if not order_id or not desc:
                    reject_rows([data], file, "missing order id or description", rejected_dir)
                    continue
                quantity = money(data.get("quantity", "1"))
                unit_price = money(data.get("unit_price", "0"))
                subtotal = quantity * unit_price
                totals = order_totals.get(order_id, {})
                base = {
                    "source_adapter": "amazon_order_history_reporter",
                    "retailer": "amazon",
                    "source_owner": infer_source_owner(file),
                    "order_id": order_id,
                    "transaction_date": parse_date(data["transaction_date"]),
                    "merchant_raw": "amazon",
                    "merchant_normalized": normalize_merchant("amazon"),
                    "item_description_raw": desc,
                    "item_description_normalized": normalize_text(desc),
                    "asin": str_or_blank(data, "asin"),
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "item_subtotal": subtotal,
                    "allocated_tax": money("0"),
                    "allocated_shipping": money("0"),
                    "allocated_fee": money("0"),
                    "item_discount": money("0"),
                    "allocated_total": subtotal,
                    "source_tax_total": totals.get("source_tax_total"),
                    "source_shipping_total": totals.get("source_shipping_total"),
                    "source_discount_total": totals.get("source_discount_total"),
                    "source_grand_total": totals.get("source_grand_total"),
                    "file_source": str(file),
                    "import_batch_id": import_batch_id,
                    "source_category_raw": str_or_blank(data, "source_category_raw"),
                }
                identity_parts = retail_identity_parts(base)
                ordinal = ordinals.next(identity_parts)
                rows.append(
                    CanonicalRetailItem(
                        item_id=stable_hash([*identity_parts, ordinal]),
                        row_fingerprint=row_fingerprint(base, base.keys()),
                        **base,
                    ).model_dump()
                )
            except Exception as exc:
                reject_rows([data], file, f"row parse error: {exc}", rejected_dir)
    df = pd.DataFrame(rows)
    return allocate_order_amounts(df) if not df.empty else df


def _normalize_item_columns(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns={col: str(col).strip().lower() for col in raw.columns})
    df = df.rename(
        columns={
            "order id": "order_id",
            "order date": "transaction_date",
            "description": "item_description_raw",
            "price": "unit_price",
            "asin": "asin",
            "category": "source_category_raw",
        }
    )
    return apply_aliases(df, "retail_item")


def _has_order_level_exports(path: Path) -> bool:
    for file in source_files(path):
        raw = read_file(file)
        normalized_cols = {str(col).strip().lower() for col in raw.columns}
        if {"order id", "items", "date", "total"}.issubset(normalized_cols):
            return True
    return False


def _load_order_level_exports(path: Path, import_batch_id: str) -> pd.DataFrame:
    rows: list[dict] = []
    ordinals = DuplicateOrdinalTracker()
    rejected_dir = Path("data/rejected")
    for file in source_files(path):
        raw = read_file(file)
        normalized_cols = {str(col).strip().lower() for col in raw.columns}
        if not {"order id", "items", "date", "total"}.issubset(normalized_cols):
            continue
        df = raw.rename(columns={col: str(col).strip().lower() for col in raw.columns})
        for _, raw_row in df.iterrows():
            data = raw_row.to_dict()
            try:
                order_id = str_or_blank(data, "order id")
                if order_id.lower() == "order id":
                    continue
                items = _clean_items(data.get("items"))
                total = money(data.get("total"))
                tax = money(data.get("tax", "0"))
                shipping = money(data.get("shipping", "0"))
                shipping_refund = money(data.get("shipping_refund", "0"))
                refund = money(data.get("refund", "0"))
                gift = money(data.get("gift", "0"))
                if not order_id or not items:
                    reject_rows([data], file, "missing order id or items", rejected_dir)
                    continue
                item_subtotal = total - tax - shipping + shipping_refund + refund + gift
                base = {
                    "source_adapter": "amazon_order_history_reporter",
                    "retailer": "amazon",
                    "source_owner": infer_source_owner(file),
                    "order_id": order_id,
                    "transaction_date": parse_date(data["date"]),
                    "merchant_raw": "amazon",
                    "merchant_normalized": normalize_merchant("amazon"),
                    "item_description_raw": items,
                    "item_description_normalized": normalize_text(items),
                    "quantity": money("1"),
                    "unit_price": item_subtotal,
                    "item_subtotal": item_subtotal,
                    "allocated_tax": tax,
                    "allocated_shipping": shipping - shipping_refund,
                    "allocated_fee": money("0"),
                    "item_discount": refund + gift,
                    "allocated_total": total,
                    "source_grand_total": total,
                    "file_source": str(file),
                    "import_batch_id": import_batch_id,
                    "source_category_raw": "order-level",
                    "needs_review": True,
                    "review_reason": "order-level Amazon reporter export without item prices",
                }
                identity_parts = retail_identity_parts(base)
                ordinal = ordinals.next(identity_parts)
                rows.append(
                    CanonicalRetailItem(
                        item_id=stable_hash([*identity_parts, ordinal]),
                        row_fingerprint=row_fingerprint(base, base.keys()),
                        **base,
                    ).model_dump()
                )
            except Exception as exc:
                reject_rows([data], file, f"row parse error: {exc}", rejected_dir)
    if rows:
        LOGGER.warning(
            "Loaded Amazon Order History Reporter order-level export(s). Reconciliation can run, but item-level categorization needs an item-level Amazon export."
        )
    return pd.DataFrame(rows)


def _clean_items(value: object) -> str:
    text = clean_string(value)
    parts = [part.strip() for part in text.split(";") if part.strip()]
    return "; ".join(parts)
