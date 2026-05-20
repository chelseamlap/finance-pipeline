from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def config_path(name: str) -> Path:
    return PACKAGE_ROOT / "config" / name


def load_yaml(name: str) -> dict:
    with config_path(name).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def normalize_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_merchant(value: object) -> str:
    text = normalize_text(value)
    aliases = {
        "amazon com": "amazon",
        "amazon marketplace": "amazon",
        "target com": "target",
        "costco wholesale": "costco",
    }
    return aliases.get(text, text)


def stable_id(parts: Iterable[object]) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def parse_date(value: object) -> date:
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="raise")
    return parsed.date()


def clean_string(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def map_simplifi_category(category: object) -> str:
    raw = clean_string(category)
    mapping = load_yaml("simplifi_category_mapping.yaml")
    return mapping.get(raw, raw)


def spending_class_for_category(category: object) -> str:
    text = clean_string(category)
    if not text:
        return ""
    mapping = load_yaml("spending_class_mapping.yaml")
    prefix = text[:2]
    if prefix in mapping.get("prefixes", {}):
        return mapping["prefixes"][prefix]
    root = text.split(":", 1)[0]
    return mapping.get("administrative", {}).get(text, mapping.get("administrative", {}).get(root, "Excluded"))


def spending_class_for_retail_category(category: object) -> str:
    text = clean_string(category)
    mapping = load_yaml("spending_class_mapping.yaml")
    return mapping.get("retail_categories", {}).get(text, "Review")


def month_mask(series: pd.Series, month: str) -> pd.Series:
    dates = pd.to_datetime(series, errors="coerce")
    return dates.dt.strftime("%Y-%m") == month
