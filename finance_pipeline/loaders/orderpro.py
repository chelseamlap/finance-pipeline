from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from finance_pipeline.loaders.generic import apply_aliases, read_tables, reject_rows, source_files, str_or_blank
from finance_pipeline.loaders.retail_common import load_retail_items
from finance_pipeline.models import CanonicalRetailItem, money
from finance_pipeline.normalize import clean_string, normalize_merchant, normalize_text, parse_date, stable_id
from finance_pipeline.reconcile import allocate_order_amounts

LOGGER = logging.getLogger(__name__)


def load(path: Path, import_batch_id: str, store: str | None = None) -> pd.DataFrame:
    retailer = (store or path.name).lower()
    files = source_files(path)
    if not files:
        return pd.DataFrame()

    orders: list[pd.DataFrame] = []
    items: list[tuple[Path, pd.DataFrame]] = []
    for file in files:
        for raw in read_tables(file):
            order_df = apply_aliases(raw.copy(), "orderpro_orders")
            item_df = apply_aliases(raw.copy(), "orderpro_items")
            is_order_report = {"order_id", "source_grand_total"}.issubset(set(order_df.columns)) and not (
                {"sku", "asin", "upc", "unit_price"} & set(item_df.columns)
            )
            is_item_report = "item_description_raw" in item_df.columns and (
                {"sku", "asin", "upc", "unit_price", "item_subtotal"} & set(item_df.columns)
            )
            if is_order_report:
                order_df["file_source"] = str(file)
                orders.append(order_df)
            elif is_item_report:
                item_df["file_source"] = str(file)
                items.append((file, item_df))
            else:
                reject_rows(raw.to_dict("records"), file, "unrecognized OrderPro report columns", Path("data/rejected"))

    if not items:
        return load_retail_items(path, import_batch_id, source_adapter="orderpro", retailer=retailer)

    orders_df = pd.concat(orders, ignore_index=True) if orders else pd.DataFrame()
    if not orders_df.empty and "order_id" in orders_df.columns:
        orders_df = orders_df[orders_df["order_id"].astype(str).str.strip() != ""].copy()
    item_rows: list[dict] = []
    for file, item_df in items:
        if "order_id" not in item_df.columns:
            reject_rows(item_df.to_dict("records"), file, "missing required columns: ['order_id']", Path("data/rejected"))
            continue
        merged = item_df
        if not orders_df.empty and "order_id" in orders_df.columns:
            merged = item_df.merge(orders_df.drop(columns=["file_source"], errors="ignore"), on="order_id", how="left", suffixes=("", "_order"))
        for idx, raw in merged.iterrows():
            data = raw.to_dict()
            try:
                desc = str_or_blank(data, "item_description_raw")
                transaction_date = data.get("transaction_date") or data.get("transaction_date_order")
                missing = [
                    name
                    for name, value in {
                        "order_id": data.get("order_id"),
                        "transaction_date": transaction_date,
                        "item_description_raw": desc,
                    }.items()
                    if not clean_string(value)
                ]
                if missing:
                    reject_rows([data], file, f"missing required columns: {missing}", Path("data/rejected"))
                    continue
                merchant = str_or_blank(data, "merchant_raw") or retailer
                quantity = money(data.get("quantity", "1"))
                unit_price = money(data.get("unit_price", "0"))
                subtotal = money(data.get("item_subtotal", "")) if clean_string(data.get("item_subtotal")) else quantity * unit_price
                discount = money(data.get("item_discount", "0"))
                tax = money(data.get("allocated_tax", "0"))
                shipping = money(data.get("allocated_shipping", "0"))
                fee = money(data.get("allocated_fee", "0"))
                item_rows.append(
                    CanonicalRetailItem(
                        item_id=stable_id(["orderpro", retailer, data.get("order_id"), idx, desc, subtotal]),
                        source_adapter="orderpro",
                        retailer=retailer,
                        order_id=str_or_blank(data, "order_id"),
                        transaction_date=parse_date(transaction_date),
                        merchant_raw=merchant,
                        merchant_normalized=normalize_merchant(merchant),
                        item_description_raw=desc,
                        item_description_normalized=normalize_text(desc),
                        sku=str_or_blank(data, "sku"),
                        asin=str_or_blank(data, "asin"),
                        upc=str_or_blank(data, "upc"),
                        quantity=quantity,
                        unit_price=unit_price,
                        item_subtotal=subtotal,
                        item_discount=discount,
                        allocated_tax=tax,
                        allocated_shipping=shipping,
                        allocated_fee=fee,
                        allocated_total=subtotal - discount + tax + shipping + fee,
                        source_order_total=_optional_money(data, "source_order_total"),
                        source_tax_total=_optional_money(data, "source_tax_total"),
                        source_discount_total=_optional_money(data, "source_discount_total"),
                        source_shipping_total=_optional_money(data, "source_shipping_total"),
                        source_fee_total=_optional_money(data, "source_fee_total"),
                        source_grand_total=_optional_money(data, "source_grand_total"),
                        file_source=str(file),
                        import_batch_id=import_batch_id,
                        source_category_raw=str_or_blank(data, "source_category_raw"),
                    ).model_dump()
                )
            except Exception as exc:
                reject_rows([data], file, f"row parse error: {exc}", Path("data/rejected"))
    df = pd.DataFrame(item_rows)
    if not df.empty:
        df = allocate_order_amounts(df)
    return df


def _optional_money(data: dict, key: str):
    value = clean_string(data.get(key))
    return money(value) if value else None
