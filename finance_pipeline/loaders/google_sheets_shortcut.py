from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from finance_pipeline.normalize import load_yaml

SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


def read_google_sheet_tables(path: Path) -> list[pd.DataFrame]:
    doc_id = read_gsheet_doc_id(path)
    session = _authorized_session()
    sheet_names = _sheet_names(session, doc_id)
    tables: list[pd.DataFrame] = []
    for sheet_name in sheet_names:
        values = _sheet_values(session, doc_id, sheet_name)
        df = _values_to_table(values)
        if df.empty:
            continue
        df["source_tab_name"] = sheet_name
        tables.append(df)
    return tables


def read_gsheet_doc_id(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    doc_id = str(payload.get("doc_id", "")).strip()
    if not doc_id:
        raise ValueError(f"Google Sheets shortcut is missing doc_id: {path}")
    return doc_id


def _authorized_session():
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
    except ImportError as exc:
        raise RuntimeError("Install google-auth to read .gsheet shortcuts.") from exc

    try:
        credentials, _ = google.auth.default(scopes=[SHEETS_READONLY_SCOPE])
    except Exception as exc:
        if exc.__class__.__name__ == "DefaultCredentialsError":
            raise RuntimeError(
                "Google credentials are required to read .gsheet shortcuts. Run gcloud auth application-default login and make sure the Google Sheets API is enabled."
            ) from exc
        raise
    return AuthorizedSession(credentials)


def _sheet_names(session, doc_id: str) -> list[str]:
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{doc_id}",
        params={"fields": "sheets.properties.title"},
    )
    _raise_for_google_response(response)
    payload = response.json()
    return [sheet["properties"]["title"] for sheet in payload.get("sheets", [])]


def _sheet_values(session, doc_id: str, sheet_name: str) -> list[list[object]]:
    encoded_range = quote(_a1_sheet_name(sheet_name), safe="")
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{doc_id}/values/{encoded_range}",
        params={"valueRenderOption": "FORMATTED_VALUE"},
    )
    _raise_for_google_response(response)
    return response.json().get("values", [])


def _values_to_table(values: list[list[object]]) -> pd.DataFrame:
    header_idx = _detect_values_header(values)
    if header_idx is None:
        return pd.DataFrame()
    header = [str(value).strip() for value in values[header_idx]]
    width = len(header)
    rows = []
    for row in values[header_idx + 1 :]:
        padded = ["" if value is None else value for value in row[:width]]
        padded.extend([""] * (width - len(padded)))
        if any(str(value).strip() for value in padded):
            rows.append(padded)
    if not rows:
        return pd.DataFrame(columns=header)
    return pd.DataFrame(rows, columns=header).dropna(how="all")


def _detect_values_header(values: list[list[object]]) -> int | None:
    aliases = load_yaml("retailer_schema_aliases.yaml")
    known_headers = {
        str(alias).strip().lower()
        for group in aliases.values()
        for names in group.values()
        for alias in names
    }
    best_row: int | None = None
    best_count = 0
    for idx, row in enumerate(values[:25]):
        row_values = {str(value).strip().lower() for value in row if str(value).strip()}
        count = len(row_values & known_headers)
        if count > best_count:
            best_row = idx
            best_count = count
    return best_row if best_count >= 2 else None


def _a1_sheet_name(sheet_name: str) -> str:
    escaped = sheet_name.replace("'", "''")
    return f"'{escaped}'"


def _raise_for_google_response(response) -> None:
    if response.status_code < 400:
        return
    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    raise RuntimeError(f"Google Sheets API request failed with {response.status_code}: {detail}")
