"""Tests for the feedback and export endpoints added in Phase C."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from pdf_to_csv import api as api_module
from pdf_to_csv.api import app
from pdf_to_csv.feedback_store import FeedbackStore


@pytest.fixture()
def temp_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Redirect the API's feedback store to a per-test SQLite file."""
    path = tmp_path / "feedback.db"
    monkeypatch.setattr(api_module, "default_db_path", lambda: path)
    return path


@pytest.fixture()
def client(temp_store, monkeypatch: pytest.MonkeyPatch):
    # Never build a real Docling converter during lifespan.
    monkeypatch.setattr(api_module, "build_converter", lambda **_: object())
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /feedback
# ---------------------------------------------------------------------------

def test_post_feedback_persists_records(client: TestClient, temp_store: Path) -> None:
    payload = {
        "records": [
            {
                "action": "edit",
                "source_file": "IMG_5945.HEIC",
                "source_bank": "generic_table",
                "statement_title": "March Chequing",
                "account_type": "chequing",
                "original": {"Date": "2025-03-02", "Amount": "886.88", "Payee": "INSURANCE"},
                "corrected": {"Date": "2025-03-02", "Amount": "-886.88", "Payee": "INSURANCE"},
                "user_comment": "wrong sign on debit",
            },
            {
                "action": "delete",
                "source_file": "IMG_5945.HEIC",
                "source_bank": "generic_table",
                "original": {"Date": "2025-02-27", "Amount": "-157637.74",
                             "Payee": "BALANCE FORWARD"},
            },
        ]
    }
    r = client.post("/feedback", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == 2
    assert len(body["ids"]) == 2

    # Persistence check via the store directly.
    store = FeedbackStore(temp_store)
    assert store.count() == 2


def test_post_feedback_rejects_empty_payload(client: TestClient) -> None:
    r = client.post("/feedback", json={"records": []})
    assert r.status_code == 400
    assert "No feedback" in r.json()["detail"]


def test_get_feedback_returns_newest_first(client: TestClient) -> None:
    for i in range(3):
        client.post("/feedback", json={
            "records": [{"action": "edit", "corrected": {"i": i}}],
        })
    r = client.get("/feedback")
    assert r.status_code == 200
    records = r.json()
    assert len(records) == 3
    # Newest first.
    assert records[0]["corrected"]["i"] == 2
    assert records[2]["corrected"]["i"] == 0


def test_get_feedback_count(client: TestClient) -> None:
    client.post("/feedback", json={"records": [{"action": "add", "corrected": {"x": 1}}]})
    r = client.get("/feedback/count")
    assert r.status_code == 200
    assert r.json() == {"count": 1}


# ---------------------------------------------------------------------------
# POST /export
# ---------------------------------------------------------------------------

_EXPORT_ROWS = [
    {
        "StatementTitle": "March Visa", "AccountType": "visa",
        "Date": "2025-03-27", "Amount": "-4.25",
        "Payee": "STARBUCKS", "Description": "STARBUCKS TORONTO",
        "Reference": "001", "CheckNumber": "",
    },
    {
        "StatementTitle": "March Visa", "AccountType": "visa",
        "Date": "2025-03-28", "Amount": "-60.00",
        "Payee": "SHELL", "Description": "SHELL TORONTO",
        "Reference": "002", "CheckNumber": "",
    },
]


def test_export_csv_renders_current_rows(client: TestClient) -> None:
    r = client.post("/export", json={"rows": _EXPORT_ROWS, "format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "transactions.csv" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("StatementTitle,AccountType,Date,Amount")
    assert lines[1].startswith("March Visa,visa,2025-03-27,-4.25")


def test_export_excel_round_trips_to_dataframe(client: TestClient) -> None:
    r = client.post("/export", json={"rows": _EXPORT_ROWS, "format": "excel",
                                     "filename": "mar_visa"})
    assert r.status_code == 200
    assert "mar_visa.xlsx" in r.headers["content-disposition"]
    df = pd.read_excel(io.BytesIO(r.content), engine="openpyxl")
    assert len(df) == 2
    assert df.iloc[0]["Payee"] == "STARBUCKS"


def test_export_rejects_empty_rows(client: TestClient) -> None:
    r = client.post("/export", json={"rows": [], "format": "csv"})
    assert r.status_code == 400
    assert "No rows" in r.json()["detail"]
