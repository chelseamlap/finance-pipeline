from __future__ import annotations

import logging
from decimal import Decimal
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

LOGGER = logging.getLogger(__name__)
REQUIRED = {"transaction_date", "item_description_raw"}


def derive_line_subtotal(data: dict, quantity: Decimal, unit_price: Decimal) -> tuple[Decimal, Decimal, str]:
    raw_subtotal = money(data.get("item_subtotal", "")) if clean_string(data.get("item_subtotal")) else quantity * unit_price
    derived_subtotal = raw_subtotal
    note = ""
    quantity_unit_total = (quantity * unit_price).quantize(Decimal("0.01"))
    if quantity > 1 and unit_price != 0 and abs(raw_subtotal - unit_price) <= Decimal("0.03"):
        derived_subtotal = quantity_unit_total
        note = "item_subtotal_derived_from_quantity_times_unit_price"
    return raw_subtotal, derived_subtotal, note


def load_retail_items(path: Path, import_batch_id: str, source_adapter: str, retailer: str) -> pd.DataFrame:
    rows: list[dict] = []
    rejected_dir = Path("data/rejected")
    ordinals = DuplicateOrdinalTracker()
    for file in source_files(path):
        df = apply_aliases(read_file(file), "retail_item")
        missing = REQUIRED - set(df.columns)
        if missing:
            LOGGER.warning("%s file %s missing required columns: %s", source_adapter, file, sorted(missing))
            reject_rows(df.to_dict("records"), file, f"missing required columns: {sorted(missing)}", rejected_dir)
            continue
        for idx, raw in df.iterrows():
            data = raw.to_dict()
            try:
                description = str_or_blank(data, "item_description_raw")
                quantity = money(data.get("quantity", "1"), Decimal("1"))
                unit_price = money(data.get("unit_price", "0"))
                raw_subtotal, subtotal, subtotal_note = derive_line_subtotal(data, quantity, unit_price)
                discount = money(data.get("item_discount", "0"))
                tax = money(data.get("allocated_tax", "0"))
                shipping = money(data.get("allocated_shipping", "0"))
                fee = money(data.get("allocated_fee", "0"))
                order_id = str_or_blank(data, "order_id")
                receipt_id = str_or_blank(data, "receipt_id")
                merchant = str_or_blank(data, "merchant_raw") or retailer
                base = {
                    "source_adapter": source_adapter,
                    "retailer": retailer,
                    "source_owner": infer_source_owner(file),
                    "order_id": order_id,
                    "receipt_id": receipt_id,
                    "transaction_date": parse_date(data["transaction_date"]),
                    "merchant_raw": merchant,
                    "merchant_normalized": normalize_merchant(merchant),
                    "item_description_raw": description,
                    "item_description_normalized": normalize_text(description),
                    "sku": str_or_blank(data, "sku"),
                    "asin": str_or_blank(data, "asin"),
                    "upc": str_or_blank(data, "upc"),
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "item_subtotal_raw": raw_subtotal,
                    "line_subtotal_derived": subtotal,
                    "item_subtotal": subtotal,
                    "item_discount": discount,
                    "allocated_tax": tax,
                    "allocated_shipping": shipping,
                    "allocated_fee": fee,
                    "allocated_total": subtotal - discount + tax + shipping + fee,
                    "item_subtotal_derivation_notes": subtotal_note,
                    "source_order_total": _optional_money(data, "source_order_total"),
                    "source_tax_total": _optional_money(data, "source_tax_total"),
                    "source_discount_total": _optional_money(data, "source_discount_total"),
                    "source_shipping_total": _optional_money(data, "source_shipping_total"),
                    "source_fee_total": _optional_money(data, "source_fee_total"),
                    "source_grand_total": _optional_money(data, "source_grand_total"),
                    "file_source": str(file),
                    "import_batch_id": import_batch_id,
                    "source_category_raw": str_or_blank(data, "source_category_raw"),
                }
                identity_parts = retail_identity_parts(base)
                ordinal = ordinals.next(identity_parts)
                item = CanonicalRetailItem(
                    item_id=stable_hash([*identity_parts, ordinal]),
                    row_fingerprint=row_fingerprint(base, base.keys()),
                    **base,
                )
                rows.append(item.model_dump())
            except Exception as exc:
                reject_rows([data], file, f"row parse error: {exc}", rejected_dir)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = allocate_order_amounts(df)
    return df


def _optional_money(data: dict, key: str):
    value = clean_string(data.get(key))
    return money(value) if value else None
