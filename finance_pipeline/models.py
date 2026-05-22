from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


TWOPLACES = Decimal("0.01")


def money(value: object, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "null"}:
        return default
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.replace("$", "").replace(",", "").replace("(", "").replace(")", "")
    result = Decimal(cleaned)
    if negative:
        result = -result
    return result.quantize(TWOPLACES)


class CanonicalTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    row_fingerprint: str = ""
    source: str = "simplifi"
    account: str = ""
    posted_date: date
    merchant_raw: str
    merchant_normalized: str
    amount: Decimal
    simplifi_category: str = ""
    simplifi_category_mapped: str = ""
    spending_class: str = ""
    notes: str = ""
    file_source: str
    import_batch_id: str


class CanonicalRetailItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    row_fingerprint: str = ""
    source_adapter: str
    retailer: str
    source_owner: str = ""
    order_id: str = ""
    receipt_id: str = ""
    transaction_date: date
    merchant_raw: str = ""
    merchant_normalized: str = ""
    item_description_raw: str = ""
    item_description_normalized: str = ""
    sku: str = ""
    asin: str = ""
    upc: str = ""
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    item_subtotal_raw: Decimal = Decimal("0")
    line_subtotal_derived: Decimal = Decimal("0")
    item_subtotal: Decimal = Decimal("0")
    item_discount: Decimal = Decimal("0")
    allocated_tax: Decimal = Decimal("0")
    allocated_shipping: Decimal = Decimal("0")
    allocated_fee: Decimal = Decimal("0")
    allocated_total: Decimal = Decimal("0")
    item_subtotal_derivation_notes: str = ""
    component_allocation_notes: str = ""
    dedupe_notes: str = ""
    source_order_total: Optional[Decimal] = None
    source_tax_total: Optional[Decimal] = None
    source_discount_total: Optional[Decimal] = None
    source_shipping_total: Optional[Decimal] = None
    source_fee_total: Optional[Decimal] = None
    source_grand_total: Optional[Decimal] = None
    matched_simplifi_transaction_id: str = ""
    household_category: str = "Unknown_Review"
    spending_class: str = ""
    category_confidence: str = "unknown"
    category_rule_id: str = ""
    needs_review: bool = False
    review_reason: str = ""
    file_source: str
    import_batch_id: str
    source_category_raw: str = ""


TRANSACTION_COLUMNS = [
    "transaction_id",
    "row_fingerprint",
    "source",
    "account",
    "posted_date",
    "merchant_raw",
    "merchant_normalized",
    "amount",
    "simplifi_category",
    "simplifi_category_mapped",
    "spending_class",
    "notes",
    "file_source",
    "import_batch_id",
]

RETAIL_ITEM_COLUMNS = [
    "item_id",
    "row_fingerprint",
    "source_adapter",
    "retailer",
    "source_owner",
    "order_id",
    "receipt_id",
    "transaction_date",
    "merchant_raw",
    "merchant_normalized",
    "item_description_raw",
    "item_description_normalized",
    "sku",
    "asin",
    "upc",
    "quantity",
    "unit_price",
    "item_subtotal_raw",
    "line_subtotal_derived",
    "item_subtotal",
    "item_discount",
    "allocated_tax",
    "allocated_shipping",
    "allocated_fee",
    "allocated_total",
    "item_subtotal_derivation_notes",
    "component_allocation_notes",
    "dedupe_notes",
    "source_order_total",
    "source_tax_total",
    "source_discount_total",
    "source_shipping_total",
    "source_fee_total",
    "source_grand_total",
    "matched_simplifi_transaction_id",
    "household_category",
    "spending_class",
    "category_confidence",
    "category_rule_id",
    "source_category_raw",
    "needs_review",
    "review_reason",
    "file_source",
    "import_batch_id",
]
