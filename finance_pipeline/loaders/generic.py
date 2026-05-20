from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from finance_pipeline.normalize import clean_string, load_yaml

LOGGER = logging.getLogger(__name__)


SUPPORTED_SUFFIXES = {".csv", ".json", ".xlsx", ".xls"}
HEADER_SCAN_ROWS = 25


def source_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)


def read_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if suffix in {".xlsx", ".xls"}:
        tables = read_excel_tables(path)
        return tables[0] if tables else pd.DataFrame()
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return pd.json_normalize(payload)
        if isinstance(payload, dict):
            for key in ("items", "orders", "receipts", "data"):
                if isinstance(payload.get(key), list):
                    return pd.json_normalize(payload[key])
            return pd.json_normalize(payload)
    raise ValueError(f"Unsupported file type: {path}")


def read_tables(path: Path) -> list[pd.DataFrame]:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return read_excel_tables(path)
    return [read_file(path)]


def read_excel_tables(path: Path) -> list[pd.DataFrame]:
    workbook = pd.ExcelFile(path)
    tables: list[pd.DataFrame] = []
    for sheet_name in workbook.sheet_names:
        header_row = _detect_excel_header(path, sheet_name)
        if header_row is None:
            continue
        df = pd.read_excel(path, sheet_name=sheet_name, header=header_row, dtype=str, keep_default_na=False)
        df = df.dropna(how="all")
        if df.empty:
            continue
        df["source_tab_name"] = sheet_name
        tables.append(df)
    return tables


def _detect_excel_header(path: Path, sheet_name: str) -> int | None:
    preview = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=HEADER_SCAN_ROWS, dtype=str, keep_default_na=False)
    aliases = load_yaml("retailer_schema_aliases.yaml")
    known_headers = {
        str(alias).strip().lower()
        for group in aliases.values()
        for names in group.values()
        for alias in names
    }
    best_row: int | None = None
    best_count = 0
    for idx, row in preview.iterrows():
        values = {str(value).strip().lower() for value in row.tolist() if str(value).strip()}
        count = len(values & known_headers)
        if count > best_count:
            best_row = int(idx)
            best_count = count
    return best_row if best_count >= 2 else 0


def alias_map(alias_group: str) -> dict[str, str]:
    aliases = load_yaml("retailer_schema_aliases.yaml").get(alias_group, {})
    result: dict[str, str] = {}
    for canonical, names in aliases.items():
        for name in names:
            result[str(name).strip().lower()] = canonical
    return result


def apply_aliases(df: pd.DataFrame, alias_group: str) -> pd.DataFrame:
    lookup = alias_map(alias_group)
    rename = {}
    used = set()
    for column in df.columns:
        canonical = lookup.get(str(column).strip().lower())
        if canonical and canonical not in used:
            rename[column] = canonical
            used.add(canonical)
    return df.rename(columns=rename)


def has_any_columns(df: pd.DataFrame, candidates: Iterable[str]) -> bool:
    cols = {str(c).strip().lower() for c in df.columns}
    return any(str(candidate).strip().lower() in cols for candidate in candidates)


def reject_rows(rows: list[dict], source_file: Path, reason: str, rejected_dir: Path) -> None:
    if not rows:
        return
    rejected_dir.mkdir(parents=True, exist_ok=True)
    safe_name = source_file.name.replace("/", "_")
    out = rejected_dir / f"{safe_name}.rejected.csv"
    df = pd.DataFrame(rows)
    if "reject_reason" in df.columns:
        df["reject_reason"] = reason
    else:
        df.insert(0, "reject_reason", reason)
    if "file_source" in df.columns:
        df["file_source"] = df["file_source"].where(df["file_source"].astype(str).str.strip() != "", str(source_file))
    else:
        df.insert(1, "file_source", str(source_file))
    df.to_csv(out, index=False)
    LOGGER.warning("Rejected %s row(s) from %s: %s", len(rows), source_file, reason)


def str_or_blank(row: dict, key: str) -> str:
    return clean_string(row.get(key, ""))
