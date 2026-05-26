from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd
import typer

from .categorize import categorize_items, taxonomy
from .export import write_month_outputs, write_review_outputs
from .logging_config import configure_logging
from .dedupe import dedupe_retail_items
from .loaders.generic import source_files
from .mappings import accept_mapping_candidate, export_mapping_tables, reject_mapping_candidate
from .models import RETAIL_ITEM_COLUMNS, TRANSACTION_COLUMNS
from .reconcile import reconcile
from .source_dates import SourceDateRecord, collect_source_max_dates
from .source_registry import load_source, reconciliation_config, registry
from .storage import BigQueryAnalyticsStore, FirestoreStateStore, MemoryStateStore

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
    check_source_dates: bool = typer.Option(True, "--check-source-dates/--skip-source-date-check", help="Print raw source max dates before loading sources."),
    persist_record_state: bool = typer.Option(True, "--persist-record-state/--skip-record-state", help="Persist transaction and retail item state when Firestore is enabled."),
    queue_mapping_candidates: bool = typer.Option(True, "--queue-mapping-candidates/--skip-mapping-queue", help="Queue unknown and conflicting mapping candidates when Firestore is enabled."),
    firestore_project: Optional[str] = typer.Option(None, "--firestore-project", help="Google Cloud project for Firestore operational state."),
    firestore_prefix: str = typer.Option("finance_pipeline", "--firestore-prefix", help="Firestore collection prefix."),
    mapping_csv: Optional[Path] = typer.Option(None, "--mapping-csv", exists=True, help="Use exported category mappings from CSV instead of Firestore for categorization."),
    bigquery_project: Optional[str] = typer.Option(None, "--bigquery-project", help="Google Cloud project for BigQuery analytics tables."),
    bigquery_dataset: str = typer.Option("finance_pipeline", "--bigquery-dataset", help="BigQuery dataset for canonical tables."),
    bigquery_location: Optional[str] = typer.Option(None, "--bigquery-location", help="Optional BigQuery dataset location."),
) -> None:
    _run_period(
        months=[month],
        import_batch_prefix="month",
        check_source_dates=check_source_dates,
        persist_record_state=persist_record_state,
        queue_mapping_candidates=queue_mapping_candidates,
        firestore_project=firestore_project,
        firestore_prefix=firestore_prefix,
        mapping_csv=mapping_csv,
        bigquery_project=bigquery_project,
        bigquery_dataset=bigquery_dataset,
        bigquery_location=bigquery_location,
        write_review=False,
    )
    typer.echo(f"Wrote monthly outputs to data/processed/{month}")


@app.command("run-period")
def run_period(
    start_month: str = typer.Option(..., "--start-month", help="First reporting month, YYYY-MM."),
    end_month: str = typer.Option(..., "--end-month", help="Last reporting month, YYYY-MM."),
    check_source_dates: bool = typer.Option(True, "--check-source-dates/--skip-source-date-check", help="Print raw source max dates before loading sources."),
    persist_record_state: bool = typer.Option(True, "--persist-record-state/--skip-record-state", help="Persist transaction and retail item state once when Firestore is enabled."),
    queue_mapping_candidates: bool = typer.Option(True, "--queue-mapping-candidates/--skip-mapping-queue", help="Queue unknown and conflicting mapping candidates when Firestore is enabled."),
    firestore_project: Optional[str] = typer.Option(None, "--firestore-project", help="Google Cloud project for Firestore operational state."),
    firestore_prefix: str = typer.Option("finance_pipeline", "--firestore-prefix", help="Firestore collection prefix."),
    mapping_csv: Optional[Path] = typer.Option(None, "--mapping-csv", exists=True, help="Use exported category mappings from CSV instead of Firestore for categorization."),
    review_output_dir: Optional[Path] = typer.Option(None, "--review-output-dir", help="Directory for consolidated review CSVs. Defaults to data/processed/runs/<run_id>/review."),
    bigquery_project: Optional[str] = typer.Option(None, "--bigquery-project", help="Google Cloud project for BigQuery analytics tables."),
    bigquery_dataset: str = typer.Option("finance_pipeline", "--bigquery-dataset", help="BigQuery dataset for canonical tables."),
    bigquery_location: Optional[str] = typer.Option(None, "--bigquery-location", help="Optional BigQuery dataset location."),
) -> None:
    months = _month_range(start_month, end_month)
    review_dir = _run_period(
        months=months,
        import_batch_prefix="period",
        check_source_dates=check_source_dates,
        persist_record_state=persist_record_state,
        queue_mapping_candidates=queue_mapping_candidates,
        firestore_project=firestore_project,
        firestore_prefix=firestore_prefix,
        mapping_csv=mapping_csv,
        bigquery_project=bigquery_project,
        bigquery_dataset=bigquery_dataset,
        bigquery_location=bigquery_location,
        write_review=True,
        review_output_dir=review_output_dir,
    )
    typer.echo(f"Wrote monthly outputs for {months[0]} through {months[-1]} to data/processed")
    typer.echo(f"Wrote consolidated review outputs to {review_dir}")


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


@app.command("export-mappings")
def export_mappings(
    firestore_project: str = typer.Option(..., "--firestore-project", help="Google Cloud project for Firestore operational state."),
    firestore_prefix: str = typer.Option("finance_pipeline", "--firestore-prefix", help="Firestore collection prefix."),
    output_dir: Path = typer.Option(Path("data/processed/mapping_review"), "--output-dir"),
) -> None:
    store = FirestoreStateStore(firestore_project, firestore_prefix)
    mappings, candidates = export_mapping_tables(store)
    store.close()
    output_dir.mkdir(parents=True, exist_ok=True)
    mappings_path = output_dir / "category_mappings.csv"
    candidates_path = output_dir / "mapping_candidates.csv"
    mappings.to_csv(mappings_path, index=False)
    candidates.to_csv(candidates_path, index=False)
    typer.echo(f"Wrote {len(mappings)} mapping(s) to {mappings_path}")
    typer.echo(f"Wrote {len(candidates)} mapping candidate(s) to {candidates_path}")


@app.command("import-reviewed-mappings")
def import_reviewed_mappings(
    review_csv: Path = typer.Option(..., "--review-csv", exists=True, help="Reviewed category_review.csv with accepted_category values."),
    firestore_project: str = typer.Option(..., "--firestore-project", help="Google Cloud project for Firestore operational state."),
    firestore_prefix: str = typer.Option("finance_pipeline", "--firestore-prefix", help="Firestore collection prefix."),
    reviewed_by: str = typer.Option("manual", "--reviewed-by"),
    category_column: str = typer.Option("accepted_category", "--category-column", help="Column containing reviewed categories to import."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and report importable mappings without writing Firestore."),
) -> None:
    df = pd.read_csv(review_csv, dtype=str, keep_default_na=False)
    rows = _reviewed_mapping_rows(df, category_column)
    if dry_run:
        typer.echo(f"Validated {len(rows)} reviewed mapping(s) from {review_csv}")
        return
    store = FirestoreStateStore(firestore_project, firestore_prefix)
    try:
        for row in rows:
            store.upsert_mapping(
                row["mapping_type"],
                row["mapping_key"],
                row["category"],
                source="review_csv",
                confidence="manual_review",
                reviewed=True,
                metadata=row["metadata"],
            )
    finally:
        store.close()
    typer.echo(f"Imported {len(rows)} reviewed mapping(s) from {review_csv}")


@app.command("accept-mapping-candidate")
def accept_candidate(
    candidate_id: str = typer.Option(..., "--candidate-id"),
    category: str = typer.Option(..., "--category"),
    firestore_project: str = typer.Option(..., "--firestore-project", help="Google Cloud project for Firestore operational state."),
    firestore_prefix: str = typer.Option("finance_pipeline", "--firestore-prefix", help="Firestore collection prefix."),
    reviewed_by: str = typer.Option("manual", "--reviewed-by"),
) -> None:
    if category not in taxonomy():
        raise typer.BadParameter(f"Category is not in taxonomy: {category}")
    store = FirestoreStateStore(firestore_project, firestore_prefix)
    try:
        candidate = accept_mapping_candidate(candidate_id, category, store, reviewed_by=reviewed_by)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()
    typer.echo(f"Accepted candidate {candidate_id}: {candidate['mapping_type']}:{candidate['mapping_key']} -> {category}")


@app.command("reject-mapping-candidate")
def reject_candidate(
    candidate_id: str = typer.Option(..., "--candidate-id"),
    firestore_project: str = typer.Option(..., "--firestore-project", help="Google Cloud project for Firestore operational state."),
    firestore_prefix: str = typer.Option("finance_pipeline", "--firestore-prefix", help="Firestore collection prefix."),
    reviewed_by: str = typer.Option("manual", "--reviewed-by"),
    note: str = typer.Option("", "--note"),
) -> None:
    store = FirestoreStateStore(firestore_project, firestore_prefix)
    try:
        reject_mapping_candidate(candidate_id, store, reviewed_by=reviewed_by, note=note)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()
    typer.echo(f"Rejected candidate {candidate_id}")


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
    sources = registry()
    store_receipt_retailers = _store_receipt_extract_retailers(Path(sources.get("store_receipt_extract", {}).get("default_path", "")))
    for source, meta in sources.items():
        default_path = Path(meta["default_path"])
        if source == "orderpro":
            base = default_path
            if not base.exists():
                continue
            for store_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                if store_dir.name in store_receipt_retailers:
                    continue
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


def _store_receipt_extract_retailers(path: Path) -> set[str]:
    if not path.exists():
        return set()
    retailers: set[str] = set()
    for file in source_files(path):
        if file.suffix.lower() == ".csv":
            try:
                df = pd.read_csv(file, dtype=str, keep_default_na=False, usecols=lambda col: col == "retailer")
            except Exception:
                continue
            if "retailer" in df.columns:
                retailers.update(df["retailer"].fillna("").str.lower().str.strip().loc[lambda s: s != ""].unique())
        elif file.suffix.lower() == ".json":
            try:
                with file.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("orders"), list):
                for order in payload["orders"]:
                    if not isinstance(order, dict):
                        continue
                    retailer = str(order.get("retailer", "")).lower().strip()
                    if retailer:
                        retailers.add(retailer)
    return retailers & {"target", "costco"}


def _run_period(
    months: list[str],
    import_batch_prefix: str,
    check_source_dates: bool,
    persist_record_state: bool,
    queue_mapping_candidates: bool,
    firestore_project: str | None,
    firestore_prefix: str,
    mapping_csv: Path | None,
    bigquery_project: str | None,
    bigquery_dataset: str,
    bigquery_location: str | None,
    write_review: bool,
    review_output_dir: Path | None = None,
) -> Path | None:
    if not months:
        raise typer.BadParameter("At least one month is required.")
    import_batch_id = f"{import_batch_prefix}-{months[0]}-to-{months[-1]}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    if check_source_dates:
        _echo_source_max_dates(collect_source_max_dates())
    state_store = FirestoreStateStore(firestore_project, firestore_prefix) if firestore_project else None
    mapping_store = _load_mapping_csv_store(mapping_csv) if mapping_csv else state_store
    analytics_store = (
        BigQueryAnalyticsStore(bigquery_project, bigquery_dataset, bigquery_location) if bigquery_project else None
    )
    try:
        transactions, items = _load_all_sources(import_batch_id)
        categorized_items, coverage = categorize_items(
            items,
            mapping_store=mapping_store,
            queue_mapping_candidates=queue_mapping_candidates,
        )
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
        for month in months:
            write_month_outputs(month, Path("data/processed") / month, transactions, categorized_items, rec, coverage)
        if state_store is not None and persist_record_state:
            state_store.upsert_transactions(transactions, import_batch_id)
            state_store.upsert_retail_items(rec.get("items", categorized_items), import_batch_id)
        if analytics_store is not None:
            analytics_store.upsert_transactions(transactions, import_batch_id)
            analytics_store.upsert_retail_items(rec.get("items", categorized_items), import_batch_id)
            for name, df in rec.items():
                if name != "items":
                    analytics_store.write_table(name, df, import_batch_id)
            analytics_store.write_table("category_rule_coverage", coverage, import_batch_id)
        if write_review:
            review_dir = review_output_dir or Path("data/processed") / "runs" / import_batch_id / "review"
            write_review_outputs(review_dir, months, transactions, rec.get("items", categorized_items), rec)
            return review_dir
        return None
    finally:
        if state_store is not None:
            state_store.close()
        if analytics_store is not None:
            analytics_store.close()


def _month_range(start_month: str, end_month: str) -> list[str]:
    try:
        periods = pd.period_range(start=start_month, end=end_month, freq="M")
    except ValueError as exc:
        raise typer.BadParameter("Months must use YYYY-MM format.") from exc
    months = [str(period) for period in periods]
    if not months:
        raise typer.BadParameter("End month must be the same as or after start month.")
    return months


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


def _load_mapping_csv_store(path: Path | None):
    if path is None:
        return None
    store = MemoryStateStore()
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {"mapping_type", "mapping_key", "category"}
    missing = required - set(df.columns)
    if missing:
        raise typer.BadParameter(f"Mapping CSV missing required column(s): {sorted(missing)}")
    base_fields = {"mapping_type", "mapping_key", "category", "source", "confidence", "reviewed"}
    for row in df.to_dict("records"):
        metadata = {key: value for key, value in row.items() if key not in base_fields and str(value).strip()}
        reviewed = str(row.get("reviewed", "False")).strip().lower() == "true"
        store.upsert_mapping(
            row["mapping_type"],
            row["mapping_key"],
            row["category"],
            source=row.get("source") or "mapping_csv",
            confidence=row.get("confidence") or "mapping_csv",
            reviewed=reviewed,
            metadata=metadata,
        )
    return store


def _reviewed_mapping_rows(df: pd.DataFrame, category_column: str) -> list[dict]:
    required = {"mapping_type", "mapping_key", category_column}
    missing = required - set(df.columns)
    if missing:
        raise typer.BadParameter(f"Reviewed mapping CSV missing required column(s): {sorted(missing)}")
    allowed = taxonomy()
    rows = []
    seen: dict[tuple[str, str], str] = {}
    for index, raw in enumerate(df.to_dict("records"), start=2):
        category = str(raw.get(category_column, "") or "").strip()
        if not category:
            continue
        if category not in allowed:
            raise typer.BadParameter(f"Row {index} category is not in taxonomy: {category}")
        mapping_type = str(raw.get("mapping_type", "") or "").strip()
        mapping_key = str(raw.get("mapping_key", "") or "").strip()
        if not mapping_type or not mapping_key:
            raise typer.BadParameter(f"Row {index} must include mapping_type and mapping_key")
        key = (mapping_type, mapping_key)
        if key in seen and seen[key] != category:
            raise typer.BadParameter(
                f"Reviewed CSV has conflicting categories for {mapping_type}:{mapping_key}: {seen[key]} and {category}"
            )
        if key in seen:
            continue
        seen[key] = category
        rows.append(
            {
                "mapping_type": mapping_type,
                "mapping_key": mapping_key,
                "category": category,
                "metadata": _review_csv_metadata(raw, category_column),
            }
        )
    return rows


def _review_csv_metadata(row: dict, category_column: str) -> dict[str, object]:
    metadata_keys = {
        "reason",
        "suggested_category",
        "item_count",
        "order_count",
        "total_allocated",
        "first_date",
        "last_date",
        "retailers",
        "source_adapters",
        "sample_original_descriptions",
        "sample_normalized_descriptions",
        "sample_item_ids",
        "sample_order_ids",
        "review_priority",
    }
    metadata = {
        f"review_csv_{key}": value
        for key, value in row.items()
        if key in metadata_keys and str(value).strip()
    }
    metadata["review_csv_category_column"] = category_column
    return metadata


if __name__ == "__main__":
    app()
