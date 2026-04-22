"""Tests for the FastAPI `/extract` endpoint.

Patch `build_converter` and `extract_transactions_from_many` so Docling never
actually runs — these tests cover the HTTP layer (multipart parsing, query
params, response shape and headers, error handling), not the pipeline itself.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from pdf_to_csv import api as api_module
from pdf_to_csv import pipeline as pipeline_module
from pdf_to_csv.api import app
from pdf_to_csv.models import TransactionRow


# ---------------------------------------------------------------------------
# Fixtures / patching
# ---------------------------------------------------------------------------

def _txn(d: str, amount: str, desc: str, source: str = "scotiabank_passport_visa") -> TransactionRow:
    y, m, dd = (int(x) for x in d.split("-"))
    return TransactionRow(
        Date=date(y, m, dd),
        Amount=Decimal(amount),
        Payee=desc.split()[0],
        Description=desc,
        Reference="",
        CheckNumber="",
        source_bank=source,
    )


@pytest.fixture()
def stub_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Stub Docling + the pipeline. Returns a function you can call from tests
    to configure what `extract_transactions_from_many` will produce for the
    current request."""
    _state: dict = {"per_file": {}}

    # Never build a real Docling converter.
    monkeypatch.setattr(api_module, "build_converter", lambda **_: object())

    def fake_extract_many(pdf_paths, *, dedupe, include_source, converter, do_ocr=False):
        results = []
        all_txns: list[TransactionRow] = []
        for p in pdf_paths:
            parser, txns, err = _state["per_file"].get(
                p.name, ("scotiabank_passport_visa", [], None)
            )
            for t in txns:
                t.source_file = p.name  # mirror what the real pipeline does
            results.append(pipeline_module.PdfExtractionResult(
                pdf_path=p, parser_name=parser, transactions=txns, error=err,
            ))
            all_txns.extend(txns)
        df = pipeline_module.transactions_to_dataframe(all_txns, include_source=include_source)
        if dedupe and not df.empty:
            df = df.drop_duplicates(subset=["Date", "Amount", "Description"]).reset_index(drop=True)
        return df, results

    monkeypatch.setattr(api_module, "extract_transactions_from_many", fake_extract_many)

    def configure(per_file: dict):
        _state["per_file"] = per_file

    return configure


@pytest.fixture()
def client():
    # TestClient triggers the lifespan context so app.state gets initialized.
    with TestClient(app) as c:
        yield c


def _pdf_bytes() -> bytes:
    # Minimal byte blob — content doesn't matter because the pipeline is stubbed.
    return b"%PDF-1.4\n%fake\n"


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_serves_html_front_end(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # Sanity-check a few markers of the drag-and-drop UI.
    body = r.text
    assert "<title>pdf-to-csv" in body
    assert 'id="drop"' in body
    assert 'id="submitBtn"' in body
    assert "/extract" in body


# ---------------------------------------------------------------------------
# /extract — happy paths
# ---------------------------------------------------------------------------

def test_extract_json_default_shape(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({
        "a.pdf": ("scotiabank_passport_visa", [
            _txn("2025-03-27", "-4.25", "STARBUCKS TORONTO"),
            _txn("2025-03-28", "-60.00", "SHELL TORONTO"),
        ], None),
    })

    r = client.post(
        "/extract",
        files=[("files", ("a.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # Top-level shape
    assert set(body.keys()) == {"summary", "files", "rows"}

    # Summary
    assert body["summary"]["files_processed"] == 1
    assert body["summary"]["files_failed"] == 0
    assert body["summary"]["rows_extracted"] == 2
    assert body["summary"]["rows_after_dedup"] == 2
    assert body["summary"]["by_parser"] == {"scotiabank_passport_visa": 2}

    # Per-file
    assert body["files"] == [{
        "filename": "a.pdf",
        "parser": "scotiabank_passport_visa",
        "rows": 2,
        "error": None,
    }]

    # Rows — canonical 6-column schema, no source columns by default
    assert set(body["rows"][0].keys()) == {
        "Date", "Amount", "Payee", "Description", "Reference", "CheckNumber",
    }
    assert body["rows"][0]["Date"] == "2025-03-27"
    assert body["rows"][0]["Amount"] == "-4.25"


def test_extract_csv_returns_text_csv_with_attachment_header(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({"a.pdf": ("scotiabank_passport_visa", [
        _txn("2025-03-27", "-4.25", "STARBUCKS"),
    ], None)})

    r = client.post(
        "/extract?format=csv",
        files=[("files", ("a.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "transactions.csv" in r.headers["content-disposition"]

    text = r.text
    lines = text.strip().splitlines()
    assert lines[0] == "Date,Amount,Payee,Description,Reference,CheckNumber"
    assert lines[1].startswith("2025-03-27,-4.25,STARBUCKS")


def test_extract_excel_returns_xlsx(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({"a.pdf": ("scotiabank_passport_visa", [
        _txn("2025-03-27", "-4.25", "STARBUCKS"),
    ], None)})

    r = client.post(
        "/extract?format=excel",
        files=[("files", ("a.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "transactions.xlsx" in r.headers["content-disposition"]
    # Parse the bytes back as an xlsx to confirm it's a valid workbook.
    df = pd.read_excel(io.BytesIO(r.content), engine="openpyxl")
    assert list(df.columns) == [
        "Date", "Amount", "Payee", "Description", "Reference", "CheckNumber",
    ]
    assert len(df) == 1


def test_extract_include_source_adds_audit_columns(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({"a.pdf": ("scotiabank_passport_visa", [
        _txn("2025-03-27", "-4.25", "STARBUCKS"),
    ], None)})

    r = client.post(
        "/extract?include_source=true",
        files=[("files", ("a.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["source_bank"] == "scotiabank_passport_visa"
    assert row["source_file"] == "a.pdf"


def test_extract_dedupes_across_multiple_uploads(client: TestClient, stub_pipeline) -> None:
    dup = _txn("2025-03-27", "-4.25", "STARBUCKS")
    stub_pipeline({
        "a.pdf": ("scotiabank_passport_visa", [dup], None),
        "b.pdf": ("scotiabank_passport_visa", [dup], None),  # identical row
    })
    r = client.post(
        "/extract",
        files=[
            ("files", ("a.pdf", _pdf_bytes(), "application/pdf")),
            ("files", ("b.pdf", _pdf_bytes(), "application/pdf")),
        ],
    )
    body = r.json()
    assert body["summary"]["rows_extracted"] == 2
    assert body["summary"]["rows_after_dedup"] == 1
    assert len(body["rows"]) == 1


# ---------------------------------------------------------------------------
# /extract — error paths
# ---------------------------------------------------------------------------

def test_extract_rejects_non_pdf(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({})
    r = client.post(
        "/extract",
        files=[("files", ("a.png", b"not a pdf", "image/png"))],
    )
    assert r.status_code == 400
    assert "Only .pdf" in r.json()["detail"]


def test_extract_rejects_empty_file(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({})
    r = client.post(
        "/extract",
        files=[("files", ("a.pdf", b"", "application/pdf"))],
    )
    assert r.status_code == 400
    assert "empty" in r.json()["detail"]


def test_extract_surfaces_per_file_errors_in_summary(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({
        "good.pdf": ("scotiabank_passport_visa", [
            _txn("2025-03-27", "-4.25", "STARBUCKS"),
        ], None),
        "bad.pdf": (None, [], "docling: kaboom"),
    })
    r = client.post(
        "/extract",
        files=[
            ("files", ("good.pdf", _pdf_bytes(), "application/pdf")),
            ("files", ("bad.pdf", _pdf_bytes(), "application/pdf")),
        ],
    )
    # Batch endpoint still returns 200 — per-file failures don't fail the whole call.
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["files_processed"] == 2
    assert body["summary"]["files_failed"] == 1
    # Good file's rows still made it into the output.
    assert body["summary"]["rows_after_dedup"] == 1
    bad_entry = next(f for f in body["files"] if f["filename"] == "bad.pdf")
    assert bad_entry["error"] == "docling: kaboom"
    assert bad_entry["parser"] is None


def test_extract_with_no_files_returns_422(client: TestClient) -> None:
    # FastAPI rejects the request at the validation layer before our handler runs.
    r = client.post("/extract")
    assert r.status_code == 422
