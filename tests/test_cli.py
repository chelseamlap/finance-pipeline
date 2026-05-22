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


def test_collect_source_max_dates_summarizes_orderpro_store_folders(tmp_path, monkeypatch):
    raw = tmp_path / "data" / "raw" / "orderpro" / "target"
    raw.mkdir(parents=True)
    shutil.copy(Path("tests/fixtures/orderpro_target_orders.csv"), raw / "orders.csv")
    shutil.copy(Path("tests/fixtures/orderpro_target_items.csv"), raw / "items.csv")
    monkeypatch.chdir(tmp_path)

    records = collect_source_max_dates(tmp_path)
    folder_records = [record for record in records if record.source == "orderpro" and record.file is None]

    assert len(folder_records) == 1
    assert folder_records[0].folder == raw
    assert folder_records[0].max_date is not None
    assert folder_records[0].max_date.isoformat() == "2026-05-04"
