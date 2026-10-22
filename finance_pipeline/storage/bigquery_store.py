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
        # WRITE_TRUNCATE, not WRITE_APPEND: this method backs snapshot-style tables
        # (unmatched transactions, items needing review, coverage stats) that are
        # fully regenerated on every run since run-period always replays full history
        # (see README). Appending duplicated every prior run's rows on top -- caught
        # after 7 runs had already left items_needing_review at ~7x its true size.
        table_id = self._table_id(table_name)
        if df.empty:
            if self._table_exists(table_id):
                self.client.query(f"TRUNCATE TABLE `{table_id}`").result()
            return 0
        job_config = self.bigquery.LoadJobConfig(
            autodetect=True,
            write_disposition=self.bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        out = _json_ready(df.copy(), run_id)
        records = _json_records(out)
        self.client.load_table_from_json(records, table_id, job_config=job_config).result()
        return len(out)

    def _merge_dataframe(self, table_name: str, df: pd.DataFrame, key_column: str, run_id: str) -> int:
        # Content-hashed key_column (transaction_id/item_id) means a source whose local
        # export covers a narrower date range than before (e.g. Simplifi's "replace, don't
        # accumulate" file only spanning the last 12 months) still only adds/updates rows
        # it actually produced this run -- MERGE never deletes a target row just because
        # this run's input didn't happen to include it.
        if df.empty:
            return 0
        out = _json_ready(df.copy(), run_id)
        records = _json_records(out)
        target = self._table_id(table_name)
        staging_name = f"_staging_{_safe_name(table_name)}_{_safe_name(run_id)}"
        staging = self._table_id(staging_name)
        job_config = self.bigquery.LoadJobConfig(
            autodetect=True,
            write_disposition=self.bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        self.client.load_table_from_json(records, staging, job_config=job_config).result()
        staging_table = self.client.get_table(staging)
        columns = [field.name for field in staging_table.schema]
        is_new_table = not self._table_exists(target)

        if is_new_table:
            copy_job = self.client.copy_table(staging, target)
            copy_job.result()

        self._ensure_tracking_columns(target)
        self._sync_schema(target, staging_table.schema)

        if is_new_table:
            # Every row is genuinely new here, so backfilling both timestamps to now is
            # accurate rather than a guess.
            self.client.query(
                f"UPDATE `{target}` SET first_seen_at = CURRENT_TIMESTAMP(), "
                "updated_at = CURRENT_TIMESTAMP() WHERE TRUE"
            ).result()
            self.client.delete_table(staging, not_found_ok=True)
            return len(out)

        update_cols = [col for col in columns if col != key_column]
        set_clause = ", ".join(f"T.`{col}` = S.`{col}`" for col in update_cols)
        set_clause = f"{set_clause}, T.`updated_at` = CURRENT_TIMESTAMP()" if set_clause else "T.`updated_at` = CURRENT_TIMESTAMP()"
        insert_cols = ", ".join(f"`{col}`" for col in [*columns, "first_seen_at", "updated_at"])
        insert_vals = ", ".join(
            [*(f"S.`{col}`" for col in columns), "CURRENT_TIMESTAMP()", "CURRENT_TIMESTAMP()"]
        )
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

    def _ensure_tracking_columns(self, target: str) -> None:
        # Idempotent: a no-op after the first call against a given table. Pre-existing
        # rows (loaded before this column existed) keep first_seen_at/updated_at as NULL
        # rather than a fabricated timestamp -- an honest "unknown," not a guess.
        self.client.query(
            f"ALTER TABLE `{target}` "
            "ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP, "
            "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP"
        ).result()

    def _sync_schema(self, target: str, staging_schema) -> None:
        # Auto-applies any column the incoming DataFrame has that the live table doesn't,
        # so a new field no longer requires a manual ALTER TABLE before the next run.
        target_table = self.client.get_table(target)
        existing = {field.name for field in target_table.schema}
        missing = [field for field in staging_schema if field.name not in existing]
        if not missing:
            return
        # Force NULLABLE regardless of what autodetect inferred from this batch: BigQuery
        # rejects ALTER TABLE ADD COLUMN with a REQUIRED field on a non-empty table, which
        # autodetect can produce if every row in this run's batch happened to be non-null.
        nullable_missing = [
            self.bigquery.SchemaField(field.name, field.field_type, mode="NULLABLE") for field in missing
        ]
        target_table.schema = list(target_table.schema) + nullable_missing
        self.client.update_table(target_table, ["schema"])

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
    return out


def _json_records(df: pd.DataFrame) -> list[dict]:
    # NaN/Decimal/date normalization happens on plain dicts, not DataFrame columns:
    # pandas' string/nullable dtypes silently re-coerce a mapped-in None back to NaN
    # on column reassignment, so values must be fixed up after to_dict() instead.
    return [{key: _json_value(value) for key, value in record.items()} for record in df.to_dict("records")]


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
