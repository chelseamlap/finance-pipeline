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
from .models import RETAIL_ITEM_COLUMNS, TRANSACTION_COLUMNS
from .reconcile import reconcile
from .source_registry import load_source, reconciliation_config, registry

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
    df = load_source(source, path, import_batch_id, store)
    if output is None:
        output_dir = Path("data/processed") / "ingested" / import_batch_id
        output_dir.mkdir(parents=True, exist_ok=True)
        kind = registry()[source]["output"]
        output = output_dir / ("canonical_transactions.csv" if kind == "transactions" else "canonical_retail_items.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)
    typer.echo(f"Wrote {len(df)} rows to {output}")


@app.command("run-month")
def run_month(month: str = typer.Option(..., "--month")) -> None:
    import_batch_id = f"month-{month}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    transactions, items = _load_all_sources(import_batch_id)
    categorized_items, coverage = categorize_items(items)
    cfg = reconciliation_config()
    rec = reconcile(
        transactions,
        categorized_items,
        date_window_days=int(cfg.get("date_window_days", 5)),
        amount_tolerance=Decimal(str(cfg.get("amount_tolerance", 0.03))),
    )
    write_month_outputs(month, Path("data/processed") / month, transactions, categorized_items, rec, coverage)
    typer.echo(f"Wrote monthly outputs to data/processed/{month}")


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
    return transactions, items


if __name__ == "__main__":
    app()
