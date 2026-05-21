from __future__ import annotations

import pandas as pd


SOURCE_PRIORITY = {
    "amazon": {
        "amazon_order_history_reporter": 100,
        "amazon_order_history_exporter": 80,
        "orderpro": 20,
    },
    "target": {
        "orderpro": 100,
        "target_manual": 50,
    },
    "costco": {
        "orderpro": 100,
        "costco_receipt_downloader": 80,
    },
}


def dedupe_retail_items(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not {"retailer", "order_id", "source_adapter"}.issubset(df.columns):
        return df
    out = df.copy()
    if "dedupe_notes" not in out.columns:
        out["dedupe_notes"] = ""

    keep_indices: list[int] = []
    for (_, _), group in out.groupby(["retailer", "order_id"], dropna=False, sort=False):
        adapters = sorted(set(group["source_adapter"].fillna("").astype(str)))
        if len(adapters) <= 1:
            keep_indices.extend(group.index.tolist())
            continue
        selected_adapter = _preferred_adapter(group)
        selected = group[group["source_adapter"].astype(str) == selected_adapter]
        dropped = [adapter for adapter in adapters if adapter != selected_adapter]
        note = f"deduped_order_sources: kept {selected_adapter}; dropped {','.join(dropped)}"
        out.loc[selected.index, "dedupe_notes"] = out.loc[selected.index, "dedupe_notes"].apply(lambda value: _append_note(value, note))
        keep_indices.extend(selected.index.tolist())

    return out.loc[sorted(keep_indices)].reset_index(drop=True)


def _preferred_adapter(group: pd.DataFrame) -> str:
    retailer = str(group["retailer"].iloc[0])
    priority = SOURCE_PRIORITY.get(retailer, {})
    summary = (
        group.groupby("source_adapter", dropna=False)
        .agg(rows=("item_id", "count"), total=("allocated_total", "sum"))
        .reset_index()
    )
    summary["priority"] = summary["source_adapter"].astype(str).map(priority).fillna(0)
    summary["abs_total"] = summary["total"].abs()
    selected = summary.sort_values(["priority", "rows", "abs_total", "source_adapter"], ascending=[False, False, False, True]).iloc[0]
    return str(selected["source_adapter"])


def _append_note(existing: object, note: str) -> str:
    text = "" if existing is None else str(existing).strip()
    if not text or text.lower() == "nan":
        return note
    if note in text:
        return text
    return f"{text}; {note}"
