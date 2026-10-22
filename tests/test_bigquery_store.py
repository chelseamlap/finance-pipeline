import sys
import types
from unittest.mock import MagicMock

import pandas as pd
import pytest


class FakeField:
    def __init__(self, name: str, field_type: str = "STRING", mode: str = "NULLABLE") -> None:
        self.name = name
        self.field_type = field_type
        self.mode = mode


class FakeTable:
    def __init__(self, schema: list[FakeField]) -> None:
        self.schema = schema


@pytest.fixture
def fake_bigquery_module(monkeypatch):
    fake_bigquery = types.SimpleNamespace(
        Client=MagicMock(),
        Dataset=MagicMock(),
        LoadJobConfig=MagicMock(side_effect=lambda **kwargs: types.SimpleNamespace(**kwargs)),
        WriteDisposition=types.SimpleNamespace(WRITE_APPEND="WRITE_APPEND", WRITE_TRUNCATE="WRITE_TRUNCATE"),
        SchemaField=FakeField,
    )
    fake_google_cloud = types.SimpleNamespace(bigquery=fake_bigquery)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_google_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", fake_bigquery)
    return fake_bigquery


def _make_store(fake_bigquery_module):
    from finance_pipeline.storage.bigquery_store import BigQueryAnalyticsStore

    client = fake_bigquery_module.Client.return_value
    client.get_dataset.return_value = object()
    store = BigQueryAnalyticsStore("proj", "dataset")
    return store, client


def test_write_table_uses_truncate_disposition(fake_bigquery_module):
    store, client = _make_store(fake_bigquery_module)
    df = pd.DataFrame([{"item_id": "a", "review_reason": "low confidence"}])

    store.write_table("items_needing_review", df, "run-2")

    job_config = client.load_table_from_json.call_args.kwargs["job_config"]
    assert job_config.write_disposition == "WRITE_TRUNCATE"


def test_write_table_truncates_existing_table_when_df_is_empty(fake_bigquery_module):
    store, client = _make_store(fake_bigquery_module)
    client.get_table.return_value = object()

    result = store.write_table("items_needing_review", pd.DataFrame(), "run-3")

    assert result == 0
    client.query.assert_called_once()
    assert "TRUNCATE TABLE" in client.query.call_args.args[0]
    client.load_table_from_json.assert_not_called()


def test_write_table_skips_truncate_when_table_never_existed(fake_bigquery_module):
    store, client = _make_store(fake_bigquery_module)
    client.get_table.side_effect = Exception("not found")

    result = store.write_table("items_needing_review", pd.DataFrame(), "run-4")

    assert result == 0
    client.query.assert_not_called()


def test_merge_dataframe_on_existing_table_never_truncates(fake_bigquery_module):
    store, client = _make_store(fake_bigquery_module)
    staging_schema = [FakeField("transaction_id"), FakeField("amount")]
    client.get_table.side_effect = lambda table_id: (
        FakeTable(staging_schema) if "_staging_" in table_id else FakeTable(staging_schema)
    )

    df = pd.DataFrame([{"transaction_id": "t1", "amount": 5.0}])
    store.upsert_transactions(df, "run-5")

    load_job_config = client.load_table_from_json.call_args.kwargs["job_config"]
    assert load_job_config.write_disposition == "WRITE_TRUNCATE"  # staging load only
    client.copy_table.assert_not_called()
    merge_query = client.query.call_args_list[-1].args[0]
    assert "merge `proj.dataset.transactions`" in merge_query
    assert "when matched then update" in merge_query
    assert "when not matched then insert" in merge_query
    assert "TRUNCATE TABLE" not in merge_query


def test_merge_dataframe_sets_tracking_timestamps(fake_bigquery_module):
    store, client = _make_store(fake_bigquery_module)
    staging_schema = [FakeField("transaction_id"), FakeField("amount")]
    client.get_table.side_effect = lambda table_id: FakeTable(staging_schema)

    store.upsert_transactions(pd.DataFrame([{"transaction_id": "t1", "amount": 5.0}]), "run-6")

    alter_query = next(c.args[0] for c in client.query.call_args_list if "ALTER TABLE" in c.args[0])
    assert "ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP" in alter_query
    assert "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP" in alter_query
    merge_query = next(c.args[0] for c in client.query.call_args_list if "merge" in c.args[0])
    assert "updated_at` = CURRENT_TIMESTAMP()" in merge_query
    assert "first_seen_at" in merge_query  # present in the not-matched insert branch


def test_merge_dataframe_backfills_timestamps_for_brand_new_table(fake_bigquery_module):
    store, client = _make_store(fake_bigquery_module)
    staging_schema = [FakeField("transaction_id"), FakeField("amount")]
    calls = {"n": 0}

    def get_table(table_id):
        if "staging_" in table_id:
            return FakeTable(staging_schema)
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("not found")
        return FakeTable(staging_schema)

    client.get_table.side_effect = get_table

    store.upsert_transactions(pd.DataFrame([{"transaction_id": "t1", "amount": 5.0}]), "run-7")

    client.copy_table.assert_called_once()
    update_query = next(c.args[0] for c in client.query.call_args_list if c.args[0].strip().startswith("UPDATE"))
    assert "first_seen_at = CURRENT_TIMESTAMP()" in update_query
    assert "updated_at = CURRENT_TIMESTAMP()" in update_query


def test_merge_dataframe_adds_missing_columns_to_existing_table(fake_bigquery_module):
    store, client = _make_store(fake_bigquery_module)
    # Autodetect infers REQUIRED when every row in a batch is non-null for a column --
    # simulate that here to prove the fix always forces NULLABLE on the applied field.
    staging_schema = [
        FakeField("transaction_id"),
        FakeField("amount"),
        FakeField("blended_category", mode="REQUIRED"),
    ]
    target_table = FakeTable([FakeField("transaction_id"), FakeField("amount")])

    def get_table(table_id):
        if "staging_" in table_id:
            return FakeTable(staging_schema)
        return target_table

    client.get_table.side_effect = get_table

    store.upsert_transactions(
        pd.DataFrame([{"transaction_id": "t1", "amount": 5.0, "blended_category": "Groceries"}]), "run-8"
    )

    client.update_table.assert_called_once()
    updated_fields = {field.name: field for field in client.update_table.call_args.args[0].schema}
    assert "blended_category" in updated_fields
    assert updated_fields["blended_category"].mode == "NULLABLE"
