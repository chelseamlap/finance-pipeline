from pathlib import Path
import shutil

from typer.testing import CliRunner

from finance_pipeline.cli import app
from finance_pipeline.source_dates import collect_source_max_dates, max_date_for_file


def test_cli_ingest_runs(tmp_path):
    runner = CliRunner()
    output = tmp_path / "out.csv"
    result = runner.invoke(
        app,
        [
            "ingest",
            "--source",
            "simplifi",
            "--path",
            str(Path("tests/fixtures/simplifi.csv")),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert output.exists()


def test_cli_source_max_dates_runs(tmp_path, monkeypatch):
    raw = tmp_path / "data" / "raw" / "simplifi"
    raw.mkdir(parents=True)
    shutil.copy(Path("tests/fixtures/simplifi.csv"), raw / "simplifi.csv")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["source-max-dates"])

    assert result.exit_code == 0, result.output
    assert "Source max dates:" in result.output
    assert "simplifi" in result.output
    assert "2026-05-05" in result.output


def test_max_date_for_file_uses_source_aliases():
    max_date, dated_rows, status = max_date_for_file("simplifi", Path("tests/fixtures/simplifi.csv"))

    assert max_date is not None
    assert max_date.isoformat() == "2026-05-05"
    assert dated_rows == 3
    assert status == "ok"


def test_run_month_can_skip_source_date_check(monkeypatch):
    import pandas as pd
    import finance_pipeline.cli as cli

    called = {"source_dates": False}

    def fail_source_dates():
        called["source_dates"] = True
        raise AssertionError("source date check should be skipped")

    monkeypatch.setattr(cli, "collect_source_max_dates", fail_source_dates)
    monkeypatch.setattr(cli, "_load_all_sources", lambda import_batch_id: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(cli, "categorize_items", lambda items: (items, pd.DataFrame()))
    monkeypatch.setattr(cli, "reconcile", lambda *args, **kwargs: {"items": pd.DataFrame()})
    monkeypatch.setattr(cli, "write_month_outputs", lambda *args, **kwargs: None)
    runner = CliRunner()

    result = runner.invoke(app, ["run-month", "--month", "2026-05", "--skip-source-date-check"])

    assert result.exit_code == 0, result.output
    assert called["source_dates"] is False


def test_run_period_loads_sources_once_and_writes_each_month(monkeypatch, tmp_path):
    import pandas as pd
    import finance_pipeline.cli as cli

    calls = {"load": 0, "months": [], "review_dir": None}

    def fake_load(import_batch_id):
        calls["load"] += 1
        return pd.DataFrame(), pd.DataFrame({"item_id": ["i1"]})

    def fake_write_month(month, out_dir, transactions, items, rec, coverage):
        calls["months"].append(month)

    def fake_write_review(out_dir, months, transactions, items, rec):
        calls["review_dir"] = out_dir

    monkeypatch.setattr(cli, "_load_all_sources", fake_load)
    monkeypatch.setattr(cli, "categorize_items", lambda items: (items, pd.DataFrame()))
    monkeypatch.setattr(cli, "reconcile", lambda *args, **kwargs: {"items": pd.DataFrame()})
    monkeypatch.setattr(cli, "write_month_outputs", fake_write_month)
    monkeypatch.setattr(cli, "write_review_outputs", fake_write_review)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-period",
            "--start-month",
            "2026-03",
            "--end-month",
            "2026-05",
            "--skip-source-date-check",
            "--review-output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["load"] == 1
    assert calls["months"] == ["2026-03", "2026-04", "2026-05"]
    assert calls["review_dir"] == tmp_path


def test_load_all_sources_loads_each_registered_source_once(monkeypatch, tmp_path):
    import pandas as pd
    import finance_pipeline.cli as cli

    raw = tmp_path / "raw"
    (raw / "store_receipt_extract").mkdir(parents=True)
    (raw / "amazon_order_history_reporter").mkdir(parents=True)
    (raw / "store_receipt_extract" / "orders_target.csv").write_text(
        "retailer,order_id,ordered_at,total\ntarget,T-1,2026-05-01,1\n"
    )
    calls = []

    monkeypatch.setattr(
        cli,
        "registry",
        lambda: {
            "store_receipt_extract": {
                "loader": "finance_pipeline.loaders.store_receipt_extract",
                "output": "retail_items",
                "default_path": str(raw / "store_receipt_extract"),
            },
            "amazon_order_history_reporter": {
                "loader": "finance_pipeline.loaders.amazon_order_history_reporter",
                "output": "retail_items",
                "default_path": str(raw / "amazon_order_history_reporter"),
            },
        },
    )

    def fake_load_source(source, path, import_batch_id, store=None):
        calls.append((source, path.name, store))
        return pd.DataFrame([{"item_id": f"{source}-{store or 'all'}"}])

    monkeypatch.setattr(cli, "load_source", fake_load_source)
    monkeypatch.setattr(cli, "dedupe_retail_items", lambda items: items)

    _, items = cli._load_all_sources("batch")

    assert ("store_receipt_extract", "store_receipt_extract", None) in calls
    assert ("amazon_order_history_reporter", "amazon_order_history_reporter", None) in calls
    assert items["item_id"].tolist() == ["store_receipt_extract-all", "amazon_order_history_reporter-all"]


