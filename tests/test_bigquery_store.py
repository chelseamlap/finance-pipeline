import sys
import types
from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture
def fake_bigquery_module(monkeypatch):
    fake_bigquery = types.SimpleNamespace(
        Client=MagicMock(),
        Dataset=MagicMock(),
        LoadJobConfig=MagicMock(side_effect=lambda **kwargs: types.SimpleNamespace(**kwargs)),
        WriteDisposition=types.SimpleNamespace(WRITE_APPEND="WRITE_APPEND", WRITE_TRUNCATE="WRITE_TRUNCATE"),
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
