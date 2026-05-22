from __future__ import annotations

from typing import Any, Protocol

import pandas as pd


class StateStore(Protocol):
    def upsert_transactions(self, df: pd.DataFrame, run_id: str) -> int: ...

    def upsert_retail_items(self, df: pd.DataFrame, run_id: str) -> int: ...

    def upsert_mapping(
        self,
        mapping_type: str,
        mapping_key: str,
        category: str,
        source: str,
        confidence: str = "manual",
        reviewed: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def get_mapping(self, mapping_type: str, mapping_key: str) -> dict | None: ...

    def list_mappings(self) -> list[dict]: ...

    def upsert_mapping_candidate(self, candidate: dict[str, Any]) -> None: ...

    def list_mapping_candidates(self) -> list[dict]: ...

    def close(self) -> None: ...


class AnalyticsStore(Protocol):
    def upsert_transactions(self, df: pd.DataFrame, run_id: str) -> int: ...

    def upsert_retail_items(self, df: pd.DataFrame, run_id: str) -> int: ...

    def write_table(self, table_name: str, df: pd.DataFrame, run_id: str) -> int: ...

    def close(self) -> None: ...
