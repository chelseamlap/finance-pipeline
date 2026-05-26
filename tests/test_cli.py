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


def test_cli_import_reviewed_mappings_upserts_accepted_categories(tmp_path, monkeypatch):
    import finance_pipeline.cli as cli

    review_csv = tmp_path / "category_review.csv"
    review_csv.write_text(
        "mapping_type,mapping_key,reason,accepted_category,item_count,sample_original_descriptions\n"
        "description,target:mystery bar,unknown_category,Groceries,2,Mystery Bar\n"
        "description,target:skip me,unknown_category,,1,Skip Me\n"
    )
    store = FakeMappingStore()
    monkeypatch.setattr(cli, "FirestoreStateStore", lambda project, prefix: store)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "import-reviewed-mappings",
            "--review-csv",
            str(review_csv),
            "--firestore-project",
            "test-project",
            "--reviewed-by",
            "chelsea",
        ],
    )

    assert result.exit_code == 0, result.output
    mapping = store.get_mapping("description", "target:mystery bar")
    assert mapping["category"] == "Groceries"
    assert mapping["source"] == "review_csv"
    assert mapping["confidence"] == "manual_review"
    assert mapping["reviewed"] is True
    assert mapping["review_csv_reason"] == "unknown_category"
    assert mapping["review_csv_sample_original_descriptions"] == "Mystery Bar"
    assert store.get_mapping("description", "target:skip me") is None


def test_cli_import_reviewed_mappings_dry_run_does_not_write(tmp_path, monkeypatch):
    import finance_pipeline.cli as cli

    review_csv = tmp_path / "category_review.csv"
    review_csv.write_text("mapping_type,mapping_key,accepted_category\ndescription,target:mystery bar,Groceries\n")
    store = FakeMappingStore()
    monkeypatch.setattr(cli, "FirestoreStateStore", lambda project, prefix: store)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "import-reviewed-mappings",
            "--review-csv",
            str(review_csv),
            "--firestore-project",
            "test-project",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Validated 1 reviewed mapping" in result.output
    assert store.get_mapping("description", "target:mystery bar") is None


def test_cli_import_reviewed_mappings_rejects_invalid_category(tmp_path):
    review_csv = tmp_path / "category_review.csv"
    review_csv.write_text("mapping_type,mapping_key,accepted_category\ndescription,target:mystery bar,Nope\n")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "import-reviewed-mappings",
            "--review-csv",
            str(review_csv),
            "--firestore-project",
            "test-project",
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    assert "category is not in taxonomy" in result.output


def test_cli_import_reviewed_mappings_rejects_conflicting_duplicate(tmp_path):
    review_csv = tmp_path / "category_review.csv"
    review_csv.write_text(
        "mapping_type,mapping_key,accepted_category\n"
        "description,target:mystery bar,Groceries\n"
        "description,target:mystery bar,Household\n"
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "import-reviewed-mappings",
            "--review-csv",
            str(review_csv),
            "--firestore-project",
            "test-project",
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    assert "conflicting categories" in result.output


def test_run_month_can_skip_source_date_check(monkeypatch):
    import pandas as pd
    import finance_pipeline.cli as cli

    called = {"source_dates": False}

    def fail_source_dates():
        called["source_dates"] = True
        raise AssertionError("source date check should be skipped")

    monkeypatch.setattr(cli, "collect_source_max_dates", fail_source_dates)
    monkeypatch.setattr(cli, "_load_all_sources", lambda import_batch_id: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(cli, "categorize_items", lambda items, mapping_store=None, queue_mapping_candidates=True: (items, pd.DataFrame()))
    monkeypatch.setattr(cli, "reconcile", lambda *args, **kwargs: {"items": pd.DataFrame()})
    monkeypatch.setattr(cli, "write_month_outputs", lambda *args, **kwargs: None)
    runner = CliRunner()

    result = runner.invoke(app, ["run-month", "--month", "2026-05", "--skip-source-date-check"])

    assert result.exit_code == 0, result.output
    assert called["source_dates"] is False


def test_run_month_can_skip_record_state_persistence(monkeypatch):
    import pandas as pd
    import finance_pipeline.cli as cli

    store = CountingStateStore()
    monkeypatch.setattr(cli, "FirestoreStateStore", lambda project, prefix: store)
    monkeypatch.setattr(cli, "_load_all_sources", lambda import_batch_id: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(cli, "categorize_items", lambda items, mapping_store=None, queue_mapping_candidates=True: (items, pd.DataFrame()))
    monkeypatch.setattr(cli, "reconcile", lambda *args, **kwargs: {"items": pd.DataFrame()})
    monkeypatch.setattr(cli, "write_month_outputs", lambda *args, **kwargs: None)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-month",
            "--month",
            "2026-05",
            "--firestore-project",
            "test-project",
            "--skip-source-date-check",
            "--skip-record-state",
        ],
    )

    assert result.exit_code == 0, result.output
    assert store.transaction_upserts == 0
    assert store.retail_item_upserts == 0
    assert store.closed is True


def test_run_month_can_skip_mapping_candidate_queue(monkeypatch):
    import pandas as pd
    import finance_pipeline.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "FirestoreStateStore", lambda project, prefix: CountingStateStore())
    monkeypatch.setattr(cli, "_load_all_sources", lambda import_batch_id: (pd.DataFrame(), pd.DataFrame({"item_id": ["i1"]})))

    def fake_categorize(items, mapping_store=None, queue_mapping_candidates=True):
        seen["queue_mapping_candidates"] = queue_mapping_candidates
        return items, pd.DataFrame()

    monkeypatch.setattr(cli, "categorize_items", fake_categorize)
    monkeypatch.setattr(cli, "reconcile", lambda *args, **kwargs: {"items": pd.DataFrame()})
    monkeypatch.setattr(cli, "write_month_outputs", lambda *args, **kwargs: None)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-month",
            "--month",
            "2026-05",
            "--firestore-project",
            "test-project",
            "--skip-source-date-check",
            "--skip-record-state",
            "--skip-mapping-queue",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["queue_mapping_candidates"] is False


def test_run_month_can_use_mapping_csv(monkeypatch, tmp_path):
    import pandas as pd
    import finance_pipeline.cli as cli

    mapping_csv = tmp_path / "category_mappings.csv"
    mapping_csv.write_text(
        "mapping_type,mapping_key,category,source,confidence,reviewed,original_item_description\n"
        "description,target:whole milk,Groceries,candidate_review,manual_review,True,Whole Milk\n"
    )
    seen = {}
    monkeypatch.setattr(cli, "_load_all_sources", lambda import_batch_id: (pd.DataFrame(), pd.DataFrame({"item_id": ["i1"]})))

    def fake_categorize(items, mapping_store=None, queue_mapping_candidates=True):
        seen["mapping"] = mapping_store.get_mapping("description", "target:whole milk")
        return items, pd.DataFrame()

    monkeypatch.setattr(cli, "categorize_items", fake_categorize)
    monkeypatch.setattr(cli, "reconcile", lambda *args, **kwargs: {"items": pd.DataFrame()})
    monkeypatch.setattr(cli, "write_month_outputs", lambda *args, **kwargs: None)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-month",
            "--month",
            "2026-05",
            "--mapping-csv",
            str(mapping_csv),
            "--skip-source-date-check",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["mapping"]["category"] == "Groceries"
    assert seen["mapping"]["original_item_description"] == "Whole Milk"


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
    monkeypatch.setattr(cli, "categorize_items", lambda items, mapping_store=None, queue_mapping_candidates=True: (items, pd.DataFrame()))
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


def test_load_all_sources_skips_orderpro_stores_replaced_by_store_receipt_extract(monkeypatch, tmp_path):
    import pandas as pd
    import finance_pipeline.cli as cli

    raw = tmp_path / "raw"
    (raw / "store_receipt_extract").mkdir(parents=True)
    (raw / "orderpro" / "target").mkdir(parents=True)
    (raw / "orderpro" / "costco").mkdir(parents=True)
    (raw / "orderpro" / "amazon").mkdir(parents=True)
    (raw / "store_receipt_extract" / "orders_target.csv").write_text(
        "retailer,order_id,ordered_at,total\ntarget,T-1,2026-05-01,1\n"
    )
    (raw / "store_receipt_extract" / "orders_costco.csv").write_text(
        "retailer,order_id,ordered_at,total\ncostco,C-1,2026-05-01,1\n"
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
            "orderpro": {
                "loader": "finance_pipeline.loaders.orderpro",
                "output": "retail_items",
                "default_path": str(raw / "orderpro"),
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
    assert ("orderpro", "amazon", "amazon") in calls
    assert ("orderpro", "target", "target") not in calls
    assert ("orderpro", "costco", "costco") not in calls
    assert items["item_id"].tolist() == ["store_receipt_extract-all", "orderpro-amazon"]


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


class CountingStateStore(FakeMappingStore):
    def __init__(self):
        super().__init__()
        self.transaction_upserts = 0
        self.retail_item_upserts = 0
        self.closed = False

    def upsert_transactions(self, df, run_id):
        self.transaction_upserts += 1
        return len(df)

    def upsert_retail_items(self, df, run_id):
        self.retail_item_upserts += 1
        return len(df)

    def close(self):
        self.closed = True
