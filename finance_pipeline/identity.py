from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .normalize import clean_string, normalize_text


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(parts: Iterable[object], length: int = 32) -> str:
    payload = "|".join(_canonical(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def row_fingerprint(row: dict, fields: Iterable[str]) -> str:
    payload = {field: _canonical(row.get(field)) for field in fields}
    return stable_hash([json.dumps(payload, sort_keys=True, separators=(",", ":"))], length=64)


class DuplicateOrdinalTracker:
    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)

    def next(self, parts: Iterable[object]) -> int:
        key = stable_hash(parts, length=64)
        ordinal = self._counts[key]
        self._counts[key] += 1
        return ordinal


def retail_identity_parts(row: dict) -> list[object]:
    item_key = clean_string(row.get("asin")) or clean_string(row.get("upc")) or clean_string(row.get("sku"))
    if not item_key:
        item_key = normalize_text(row.get("item_description_raw") or row.get("item_description_normalized"))
    return [
        "retail",
        clean_string(row.get("retailer")),
        clean_string(row.get("source_owner")),
        clean_string(row.get("order_id")),
        clean_string(row.get("receipt_id")),
        item_key,
        _canonical(row.get("transaction_date")),
        _canonical(row.get("quantity")),
        _canonical(row.get("allocated_total") or row.get("item_subtotal")),
    ]


def transaction_identity_parts(row: dict) -> list[object]:
    return [
        "transaction",
        clean_string(row.get("source") or "simplifi"),
        clean_string(row.get("account")),
        _canonical(row.get("posted_date")),
        normalize_text(row.get("merchant_raw") or row.get("merchant_normalized")),
        _canonical(row.get("amount")),
        normalize_text(row.get("notes")),
    ]


def mapping_keys_for_retail_item(row: dict) -> list[tuple[str, str]]:
    keys = item_mapping_keys_for_retail_item(row)
    merchant = normalize_text(row.get("merchant_raw") or row.get("merchant_normalized"))
    if merchant:
        keys.append(("merchant", merchant))
    return keys


def item_mapping_keys_for_retail_item(row: dict) -> list[tuple[str, str]]:
    retailer = normalize_text(row.get("retailer"))
    keys: list[tuple[str, str]] = []
    for key_type, field in [("asin", "asin"), ("upc", "upc"), ("sku", "sku")]:
        value = clean_string(row.get(field))
        if value:
            keys.append((key_type, f"{retailer}:{value}" if retailer else value))
    desc = normalize_text(row.get("item_description_raw") or row.get("item_description_normalized"))
    if desc:
        keys.append(("description", f"{retailer}:{desc}" if retailer else desc))
    return keys


def infer_source_owner(path: Path) -> str:
    text = normalize_text(path.stem)
    if "michael" in text:
        return "michael"
    if "chelsea" in text:
        return "chelsea"
    return ""


def _canonical(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.quantize(Decimal("0.01")), "f")
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text
