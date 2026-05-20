from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from finance_pipeline.loaders.generic import apply_aliases, read_file, reject_rows, source_files, str_or_blank
from finance_pipeline.models import CanonicalTransaction, money
from finance_pipeline.normalize import map_simplifi_category, normalize_merchant, parse_date, spending_class_for_category, stable_id

LOGGER = logging.getLogger(__name__)
REQUIRED = {"posted_date", "merchant_raw", "amount"}


def load(path: Path, import_batch_id: str, store: str | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    rejected_dir = Path("data/rejected")
    for file in source_files(path):
        df = apply_aliases(read_file(file), "simplifi")
        missing = REQUIRED - set(df.columns)
        if missing:
            LOGGER.warning("Simplifi file %s missing required columns: %s", file, sorted(missing))
            reject_rows(df.to_dict("records"), file, f"missing required columns: {sorted(missing)}", rejected_dir)
            continue
        for idx, raw in df.iterrows():
            data = raw.to_dict()
            try:
                merchant = str_or_blank(data, "merchant_raw")
                txn_id = str_or_blank(data, "transaction_id") or stable_id([file, idx, data.get("posted_date"), merchant, data.get("amount")])
                category_raw = str_or_blank(data, "simplifi_category")
                category_mapped = map_simplifi_category(category_raw)
                rows.append(
                    CanonicalTransaction(
                        transaction_id=txn_id,
                        account=str_or_blank(data, "account"),
                        posted_date=parse_date(data["posted_date"]),
                        merchant_raw=merchant,
                        merchant_normalized=normalize_merchant(merchant),
                        amount=money(data["amount"]),
                        simplifi_category=category_raw,
                        simplifi_category_mapped=category_mapped,
                        spending_class=spending_class_for_category(category_mapped),
                        notes=str_or_blank(data, "notes"),
                        file_source=str(file),
                        import_batch_id=import_batch_id,
                    ).model_dump()
                )
            except Exception as exc:
                reject_rows([data], file, f"row parse error: {exc}", rejected_dir)
    return pd.DataFrame(rows)
