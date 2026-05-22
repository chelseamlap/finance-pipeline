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


def test_cli_export_mappings_writes_mapping_review_files(tmp_path, monkeypatch):
    import finance_pipeline.cli as cli

    monkeypatch.setattr(cli, "FirestoreStateStore", lambda project, prefix: FakeMappingStore())
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "export-mappings",
            "--firestore-project",
            "test-project",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "category_mappings.csv").exists()
    assert (tmp_path / "mapping_candidates.csv").exists()
    assert "target:whole milk" in (tmp_path / "category_mappings.csv").read_text()
    assert "unknown_category" in (tmp_path / "mapping_candidates.csv").read_text()


def test_run_month_can_skip_source_date_check(monkeypatch):
    import pandas as pd
    import finance_pipeline.cli as cli

    called = {"source_dates": False}

    def fail_source_dates():
        called["source_dates"] = True
        raise AssertionError("source date check should be skipped")

    monkeypatch.setattr(cli, "collect_source_max_dates", fail_source_dates)
    monkeypatch.setattr(cli, "_load_all_sources", lambda import_batch_id: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(cli, "categorize_items", lambda items, mapping_store=None: (items, pd.DataFrame()))
    monkeypatch.setattr(cli, "reconcile", lambda *args, **kwargs: {"items": pd.DataFrame()})
    monkeypatch.setattr(cli, "write_month_outputs", lambda *args, **kwargs: None)
    runner = CliRunner()

    result = runner.invoke(app, ["run-month", "--month", "2026-05", "--skip-source-date-check"])

    assert result.exit_code == 0, result.output
    assert called["source_dates"] is False


def test_cli_accept_mapping_candidate_promotes_candidate(monkeypatch):
    import finance_pipeline.cli as cli

    store = FakeMappingStore()
    store.upsert_mapping_candidate(
        {
            "candidate_id": "candidate-1",
            "mapping_type": "description",
            "mapping_key": "target:mystery object",
            "reason": "unknown_category",
            "status": "needs_review",
        }
    )
    monkeypatch.setattr(cli, "FirestoreStateStore", lambda project, prefix: store)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "accept-mapping-candidate",
            "--candidate-id",
            "candidate-1",
            "--category",
            "Household",
            "--firestore-project",
            "test-project",
        ],
    )

    assert result.exit_code == 0, result.output
    assert store.get_mapping("description", "target:mystery object")["category"] == "Household"
    assert store.get_mapping_candidate("candidate-1")["status"] == "accepted"


def test_cli_accept_mapping_candidate_rejects_unknown_taxonomy_category(monkeypatch):
    import finance_pipeline.cli as cli

    monkeypatch.setattr(cli, "FirestoreStateStore", lambda project, prefix: FakeMappingStore())
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "accept-mapping-candidate",
            "--candidate-id",
            "candidate-1",
            "--category",
            "Not_A_Category",
            "--firestore-project",
            "test-project",
        ],
    )

    assert result.exit_code != 0
    assert "Category is not in taxonomy" in result.output


def test_cli_reject_mapping_candidate_marks_candidate_rejected(monkeypatch):
    import finance_pipeline.cli as cli
    store = FakeMappingStore()
    store.upsert_mapping_candidate(
        {
            "candidate_id": "candidate-1",
            "mapping_type": "description",
            "mapping_key": "target:mystery object",
            "reason": "unknown_category",
            "status": "needs_review",
        }
    )
    monkeypatch.setattr(cli, "FirestoreStateStore", lambda project, prefix: store)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "reject-mapping-candidate",
            "--candidate-id",
            "candidate-1",
            "--firestore-project",
            "test-project",
            "--note",
            "not enough info",
        ],
    )

    assert result.exit_code == 0, result.output
    assert store.get_mapping_candidate("candidate-1")["status"] == "rejected"
    assert store.get_mapping_candidate("candidate-1")["review_note"] == "not enough info"


class FakeMappingStore:
    def __init__(self):
        self.category_mappings = {}
        self.mapping_candidates = {}

    def close(self):
        return None

    def upsert_mapping(self, mapping_type, mapping_key, category, source, confidence="manual", reviewed=True, metadata=None):
        record = {
            "mapping_type": mapping_type,
            "mapping_key": mapping_key,
            "category": category,
            "source": source,
            "confidence": confidence,
            "reviewed": reviewed,
        }
        if metadata:
            record.update(metadata)
        self.category_mappings[(mapping_type, mapping_key)] = record

    def get_mapping(self, mapping_type, mapping_key):
        return self.category_mappings.get((mapping_type, mapping_key))

    def list_mappings(self):
        return list(self.category_mappings.values()) or [
            {
                "mapping_type": "description",
                "mapping_key": "target:whole milk",
                "category": "Groceries",
            }
        ]

    def upsert_mapping_candidate(self, candidate):
        self.mapping_candidates[candidate["candidate_id"]] = candidate

    def get_mapping_candidate(self, candidate_id):
        return self.mapping_candidates.get(candidate_id)

    def list_mapping_candidates(self):
        return list(self.mapping_candidates.values()) or [
            {
                "candidate_id": "candidate-1",
                "mapping_type": "description",
                "mapping_key": "target:mystery object",
                "reason": "unknown_category",
                "status": "needs_review",
            }
        ]
