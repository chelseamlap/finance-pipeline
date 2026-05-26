from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from finance_pipeline.loaders.generic import apply_aliases, read_tables, source_files
from finance_pipeline.source_registry import registry


DATE_COLUMNS = ("posted_date", "transaction_date", "ordered_at")


@dataclass(frozen=True)
class SourceDateRecord:
    source: str
    folder: Path
    file: Path | None
    max_date: date | None
    dated_rows: int
    status: str = "ok"


def collect_source_max_dates(root: Path = Path(".")) -> list[SourceDateRecord]:
    records: list[SourceDateRecord] = []
    for source, meta in registry().items():
        default_path = root / Path(meta["default_path"])
        folders = _source_folders(source, default_path)
        if not folders:
            records.append(
                SourceDateRecord(
                    source=source,
                    folder=default_path,
                    file=None,
                    max_date=None,
                    dated_rows=0,
                    status="missing",
                )
            )
            continue
        for folder in folders:
            records.extend(_collect_folder_dates(source, folder))
    return records


def _source_folders(source: str, default_path: Path) -> list[Path]:
    if not default_path.exists():
        return []
    if default_path.is_file():
        return [default_path]
    if source == "orderpro":
        return sorted(path for path in default_path.iterdir() if path.is_dir())
    return [default_path]


def _collect_folder_dates(source: str, folder: Path) -> list[SourceDateRecord]:
    files = source_files(folder)
    if not files:
        return [
            SourceDateRecord(
                source=source,
                folder=folder,
                file=None,
                max_date=None,
                dated_rows=0,
                status="empty",
            )
        ]

    records: list[SourceDateRecord] = []
    folder_max: date | None = None
    folder_rows = 0
    folder_status = "ok"
    for file in files:
        max_date, dated_rows, status = max_date_for_file(source, file)
        if max_date is not None and (folder_max is None or max_date > folder_max):
            folder_max = max_date
        folder_rows += dated_rows
        if status != "ok":
            folder_status = "partial" if folder_status == "ok" else folder_status
        records.append(
            SourceDateRecord(
                source=source,
                folder=folder,
                file=file,
                max_date=max_date,
                dated_rows=dated_rows,
                status=status,
            )
        )

    return [
        SourceDateRecord(
            source=source,
            folder=folder,
            file=None,
            max_date=folder_max,
            dated_rows=folder_rows,
            status=folder_status,
        ),
        *records,
    ]


def max_date_for_file(source: str, file: Path) -> tuple[date | None, int, str]:
    max_date: date | None = None
    dated_rows = 0
    try:
        tables = read_tables(file)
    except Exception as exc:
        return None, 0, f"error: {exc}"

    for raw in tables:
        for alias_group in _alias_groups(source):
            df = apply_aliases(raw.copy(), alias_group)
            table_dated_rows = 0
            table_max_date: date | None = None
            for column in DATE_COLUMNS:
                if column not in df.columns:
                    continue
                parsed = pd.to_datetime(df[column], errors="coerce", utc=True)
                valid = parsed.dropna()
                table_dated_rows += int(valid.size)
                if valid.empty:
                    continue
                candidate = valid.max().date()
                if table_max_date is None or candidate > table_max_date:
                    table_max_date = candidate
            if table_dated_rows:
                dated_rows += table_dated_rows
                if table_max_date is not None and (max_date is None or table_max_date > max_date):
                    max_date = table_max_date
                break
    if max_date is None:
        return None, dated_rows, "no dates"
    return max_date, dated_rows, "ok"


def _alias_groups(source: str) -> tuple[str, ...]:
    if source == "simplifi":
        return ("simplifi",)
    if source == "orderpro":
        return ("orderpro_orders", "orderpro_items", "retail_item")
    if source == "store_receipt_extract":
        return ("store_receipt_extract_orders", "store_receipt_extract_items", "retail_item")
    return ("retail_item",)
