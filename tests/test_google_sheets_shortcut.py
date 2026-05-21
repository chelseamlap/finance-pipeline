import json

from finance_pipeline.loaders import generic
from finance_pipeline.loaders.google_sheets_shortcut import read_gsheet_doc_id, read_google_sheet_tables


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def get(self, url, params=None):
        if url.endswith("/spreadsheet-123"):
            return FakeResponse(
                {
                    "sheets": [
                        {"properties": {"title": "Order History"}},
                        {"properties": {"title": "Purchased Items"}},
                    ]
                }
            )
        if "Order%20History" in url:
            return FakeResponse({"values": [["Platform", "TARGET"], ["Period", "2026"]]})
        if "Purchased%20Items" in url:
            return FakeResponse(
                {
                    "values": [
                        ["noise", "noise"],
                        ["Order Date", "Order ID", "Product Description", "Product Quantity", "Product Price"],
                        ["2026-05-01", "T-1", "Whole Milk", "1", "3.99"],
                    ]
                }
            )
        raise AssertionError(url)


def test_read_gsheet_doc_id(tmp_path):
    shortcut = tmp_path / "report.gsheet"
    shortcut.write_text(json.dumps({"doc_id": "spreadsheet-123"}))

    assert read_gsheet_doc_id(shortcut) == "spreadsheet-123"


def test_read_google_sheet_tables_from_shortcut(monkeypatch, tmp_path):
    shortcut = tmp_path / "report.gsheet"
    shortcut.write_text(json.dumps({"doc_id": "spreadsheet-123"}))
    monkeypatch.setattr("finance_pipeline.loaders.google_sheets_shortcut._authorized_session", lambda: FakeSession())

    tables = read_google_sheet_tables(shortcut)

    assert len(tables) == 1
    assert tables[0].loc[0, "Order ID"] == "T-1"
    assert tables[0].loc[0, "source_tab_name"] == "Purchased Items"


def test_generic_source_files_include_gsheet(tmp_path):
    shortcut = tmp_path / "report.gsheet"
    shortcut.write_text(json.dumps({"doc_id": "spreadsheet-123"}))

    assert generic.source_files(tmp_path) == [shortcut]
