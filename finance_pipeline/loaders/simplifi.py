from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from finance_pipeline.loaders.generic import apply_aliases, read_file, reject_rows, source_files, str_or_blank
from finance_pipeline.identity import DuplicateOrdinalTracker, row_fingerprint, stable_hash, transaction_identity_parts
from finance_pipeline.models import CanonicalTransaction, money
from finance_pipeline.normalize import map_simplifi_category, normalize_merchant, parse_date, spending_class_for_category

LOGGER = logging.getLogger(__name__)
REQUIRED = {"posted_date", "merchant_raw", "amount"}


def load(path: Path, import_batch_id: str, store: str | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    rejected_dir = Path("data/rejected")
    ordinals = DuplicateOrdinalTracker()
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
                category_raw = str_or_blank(data, "simplifi_category")
                category_mapped = map_simplifi_category(category_raw)
                base = {
                    "source": "simplifi",
                    "account": str_or_blank(data, "account"),
                    "posted_date": parse_date(data["posted_date"]),
                    "merchant_raw": merchant,
                    "merchant_normalized": normalize_merchant(merchant),
                    "amount": money(data["amount"]),
                    "simplifi_category": category_raw,
                    "simplifi_category_mapped": category_mapped,
                    "spending_class": spending_class_for_category(category_mapped),
                    "notes": str_or_blank(data, "notes"),
                    "file_source": str(file),
                    "import_batch_id": import_batch_id,
                }
                identity_parts = transaction_identity_parts(base)
                ordinal = ordinals.next(identity_parts)
                txn_id = str_or_blank(data, "transaction_id") or stable_hash([*identity_parts, ordinal])
                rows.append(
                    CanonicalTransaction(
                        transaction_id=txn_id,
                        row_fingerprint=row_fingerprint(base, base.keys()),
                        **base,
                    ).model_dump()
                )
            except Exception as exc:
                reject_rows([data], file, f"row parse error: {exc}", rejected_dir)
    return pd.DataFrame(rows)
