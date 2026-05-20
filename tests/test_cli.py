from pathlib import Path

from typer.testing import CliRunner

from finance_pipeline.cli import app


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
