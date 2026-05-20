from __future__ import annotations

from pathlib import Path

from .retail_common import load_retail_items


def load(path: Path, import_batch_id: str, store: str | None = None):
    return load_retail_items(path, import_batch_id, source_adapter="amazon_order_history_reporter", retailer="amazon")
