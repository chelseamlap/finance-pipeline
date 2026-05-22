from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd
import typer

from .categorize import categorize_items
from .export import write_month_outputs
from .logging_config import configure_logging
from .dedupe import dedupe_retail_items
from .models import RETAIL_ITEM_COLUMNS, TRANSACTION_COLUMNS
from .reconcile import reconcile
from .source_dates import SourceDateRecord, collect_source_max_dates
from .source_registry import load_source, reconciliation_config, registry
from .storage import BigQueryAnalyticsStore, FirestoreStateStore

app = typer.Typer(help="Deterministic personal finance retail parsing pipeline.")


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")):
    configure_logging(verbose)


@app.command()
def ingest(
    source: str = typer.Option(..., "--source"),
    path: Path = typer.Option(..., "--path", exists=True),
    store: Optional[str] = typer.Option(None, "--store"),
    output: Optional[Path] = typer.Option(None, "--output"),
) -> None:
    import_batch_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    try:
        df = load_source(source, path, import_batch_id, store)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if output is None:
        output_dir = Path("data/processed") / "ingested" / import_batch_id
        output_dir.mkdir(parents=True, exist_ok=True)
        kind = registry()[source]["output"]
        output = output_dir / ("canonical_transactions.csv" if kind == "transactions" else "canonical_retail_items.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)
    typer.echo(f"Wrote {len(df)} rows to {output}")


@app.command("run-month")
def run_month(
    month: str = typer.Option(..., "--month"),
    firestore_project: Optional[str] = typer.Option(None, "--firestore-project", help="Google Cloud project for Firestore operational state."),
    firestore_prefix: str = typer.Option("finance_pipeline", "--firestore-prefix", help="Firestore collection prefix."),
    bigquery_project: Optional[str] = typer.Option(None, "--bigquery-project", help="Google Cloud project for BigQuery analytics tables."),
    bigquery_dataset: str = typer.Option("finance_pipeline", "--bigquery-dataset", help="BigQuery dataset for canonical tables."),
    bigquery_location: Optional[str] = typer.Option(None, "--bigquery-location", help="Optional BigQuery dataset location."),
) -> None:
    import_batch_id = f"month-{month}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    _echo_source_max_dates(collect_source_max_dates())
    state_store = FirestoreStateStore(firestore_project, firestore_prefix) if firestore_project else None
    analytics_store = (
        BigQueryAnalyticsStore(bigquery_project, bigquery_dataset, bigquery_location) if bigquery_project else None
    )
    transactions, items = _load_all_sources(import_batch_id)
    categorized_items, coverage = categorize_items(items, mapping_store=state_store)
    cfg = reconciliation_config()
    rec = reconcile(
        transactions,
        categorized_items,
        date_window_days=int(cfg.get("date_window_days", 5)),
        amount_tolerance=Decimal(str(cfg.get("amount_tolerance", 0.03))),
        match_amount_tolerance=Decimal(str(cfg.get("match_amount_tolerance", cfg.get("amount_tolerance", 0.03)))),
        amazon_extended_date_window_days=cfg.get("amazon_extended_date_window_days"),
        amazon_extended_date_min_amount=Decimal(str(cfg.get("amazon_extended_date_min_amount", 10.00))),
    )
    write_month_outputs(month, Path("data/processed") / month, transactions, categorized_items, rec, coverage)
    if state_store is not None:
        state_store.upsert_transactions(transactions, import_batch_id)
        state_store.upsert_retail_items(rec.get("items", categorized_items), import_batch_id)
        state_store.close()
    if analytics_store is not None:
        analytics_store.upsert_transactions(transactions, import_batch_id)
        analytics_store.upsert_retail_items(rec.get("items", categorized_items), import_batch_id)
        for name, df in rec.items():
            if name != "items":
                analytics_store.write_table(name, df, import_batch_id)
        analytics_store.write_table("category_rule_coverage", coverage, import_batch_id)
        analytics_store.close()
    typer.echo(f"Wrote monthly outputs to data/processed/{month}")


@app.command("source-max-dates")
def source_max_dates() -> None:
    """Print the latest source date found in each registered raw folder and file."""
    _echo_source_max_dates(collect_source_max_dates())


@app.command("save-mapping")
def save_mapping(
    mapping_type: str = typer.Option(..., "--type", help="Mapping type, for example asin, upc, sku, description, or merchant."),
    mapping_key: str = typer.Option(..., "--key", help="Normalized mapping key."),
    category: str = typer.Option(..., "--category"),
    firestore_project: str = typer.Option(..., "--firestore-project", help="Google Cloud project for Firestore operational state."),
    firestore_prefix: str = typer.Option("finance_pipeline", "--firestore-prefix", help="Firestore collection prefix."),
) -> None:
    store = FirestoreStateStore(firestore_project, firestore_prefix)
    store.upsert_mapping(mapping_type, mapping_key, category, source="manual", confidence="manual", reviewed=True)
    store.close()
    typer.echo(f"Saved {mapping_type}:{mapping_key} -> {category}")


@app.command()
def export(month: str = typer.Option(..., "--month")) -> None:
    out_dir = Path("data/processed") / month
    if not out_dir.exists():
        raise typer.BadParameter(f"No processed month found at {out_dir}. Run run-month first.")
    expected = [
        "canonical_transactions.csv",
        "canonical_retail_items.csv",
        "monthly_category_summary.csv",
        "retailer_summary.csv",
        "store_reconciliation_summary.csv",
        "reconciliation_summary.csv",
        "reconciliation_detail.csv",
        "unmatched_simplifi_transactions.csv",
        "unmatched_retail_orders.csv",
        "items_needing_review.csv",
        "category_rule_coverage.csv",
    ]
    missing = [name for name in expected if not (out_dir / name).exists()]
    if missing:
        raise typer.BadParameter(f"Missing expected export(s): {missing}")
    typer.echo(f"Exports ready in {out_dir}")


def _load_all_sources(import_batch_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    tx_frames: list[pd.DataFrame] = []
    item_frames: list[pd.DataFrame] = []
    for source, meta in registry().items():
        default_path = Path(meta["default_path"])
        if source == "orderpro":
            base = default_path
            if not base.exists():
                continue
            for store_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                df = load_source(source, store_dir, import_batch_id, store_dir.name)
                if not df.empty:
                    item_frames.append(df)
            continue
        if not default_path.exists():
            continue
        df = load_source(source, default_path, import_batch_id)
        if df.empty:
            continue
        if meta["output"] == "transactions":
            tx_frames.append(df)
        else:
            item_frames.append(df)

    transactions = pd.concat(tx_frames, ignore_index=True) if tx_frames else pd.DataFrame(columns=TRANSACTION_COLUMNS)
    items = pd.concat(item_frames, ignore_index=True) if item_frames else pd.DataFrame(columns=RETAIL_ITEM_COLUMNS)
    items = dedupe_retail_items(items)
    return transactions, items


def _echo_source_max_dates(records: list[SourceDateRecord]) -> None:
    typer.echo("Source max dates:")
    for record in records:
        scope = "folder" if record.file is None else "file"
        path = record.folder if record.file is None else record.file
        max_date = record.max_date.isoformat() if record.max_date is not None else "-"
        typer.echo(
            f"{record.source}\t{scope}\t{path}\t{max_date}\t"
            f"{record.dated_rows} dated row(s)\t{record.status}"
        )


if __name__ == "__main__":
    app()
