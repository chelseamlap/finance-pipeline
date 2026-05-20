from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

import pandas as pd


class BigQueryAnalyticsStore:
    def __init__(self, project: str, dataset: str, location: str | None = None) -> None:
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise RuntimeError("Install google-cloud-bigquery to use BigQueryAnalyticsStore.") from exc

        self.bigquery = bigquery
        self.client = bigquery.Client(project=project, location=location)
        self.project = project
        self.dataset = dataset
        self.location = location
        self._ensure_dataset()

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()

    def upsert_transactions(self, df: pd.DataFrame, run_id: str) -> int:
        return self._merge_dataframe("transactions", df, "transaction_id", run_id)

    def upsert_retail_items(self, df: pd.DataFrame, run_id: str) -> int:
        return self._merge_dataframe("retail_items", df, "item_id", run_id)

    def write_table(self, table_name: str, df: pd.DataFrame, run_id: str) -> int:
        if df.empty:
            return 0
        out = _json_ready(df.copy(), run_id)
        table_id = self._table_id(table_name)
        job_config = self.bigquery.LoadJobConfig(
            autodetect=True,
            write_disposition=self.bigquery.WriteDisposition.WRITE_APPEND,
        )
        self.client.load_table_from_json(out.to_dict("records"), table_id, job_config=job_config).result()
        return len(out)

    def _merge_dataframe(self, table_name: str, df: pd.DataFrame, key_column: str, run_id: str) -> int:
        if df.empty:
            return 0
        out = _json_ready(df.copy(), run_id)
        target = self._table_id(table_name)
        staging_name = f"_staging_{_safe_name(table_name)}_{_safe_name(run_id)}"
        staging = self._table_id(staging_name)
        job_config = self.bigquery.LoadJobConfig(
            autodetect=True,
            write_disposition=self.bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        self.client.load_table_from_json(out.to_dict("records"), staging, job_config=job_config).result()
        if not self._table_exists(target):
            copy_job = self.client.copy_table(staging, target)
            copy_job.result()
            self.client.delete_table(staging, not_found_ok=True)
            return len(out)

        staging_table = self.client.get_table(staging)
        columns = [field.name for field in staging_table.schema]
        update_cols = [col for col in columns if col != key_column]
        set_clause = ", ".join(f"T.`{col}` = S.`{col}`" for col in update_cols)
        insert_cols = ", ".join(f"`{col}`" for col in columns)
        insert_vals = ", ".join(f"S.`{col}`" for col in columns)
        query = f"""
        merge `{target}` T
        using `{staging}` S
        on T.`{key_column}` = S.`{key_column}`
        when matched then update set {set_clause}
        when not matched then insert ({insert_cols}) values ({insert_vals})
        """
        self.client.query(query).result()
        self.client.delete_table(staging, not_found_ok=True)
        return len(out)

    def _ensure_dataset(self) -> None:
        dataset_id = f"{self.project}.{self.dataset}"
        if self._dataset_exists(dataset_id):
            return
        dataset = self.bigquery.Dataset(dataset_id)
        if self.location:
            dataset.location = self.location
        self.client.create_dataset(dataset)

    def _dataset_exists(self, dataset_id: str) -> bool:
        try:
            self.client.get_dataset(dataset_id)
            return True
        except Exception:
            return False

    def _table_exists(self, table_id: str) -> bool:
        try:
            self.client.get_table(table_id)
            return True
        except Exception:
            return False

    def _table_id(self, table_name: str) -> str:
        return f"{self.project}.{self.dataset}.{_safe_name(table_name)}"


def _json_ready(df: pd.DataFrame, run_id: str) -> pd.DataFrame:
    out = df.copy()
    out["analytics_run_id"] = run_id
    for column in out.columns:
        out[column] = out[column].map(_json_value)
    return out


def _json_value(value: object) -> object:
    if pd.isna(value):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    if not safe:
        return "table"
    if safe[0].isdigit():
        safe = f"t_{safe}"
    return safe[:1024]
