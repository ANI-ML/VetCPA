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
from pdf_to_csv.account_type import AccountType
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

    def fake_extract_many(jobs, *, dedupe, include_source, converter, do_ocr=False):
        results = []
        all_txns: list[TransactionRow] = []
        for j in jobs:
            # The pipeline now passes PdfJob objects; unwrap for the stub.
            path = j.path if hasattr(j, "path") else j
            parser, txns, err = _state["per_file"].get(
                path.name, ("scotiabank_passport_visa", [], None)
            )
            title = (j.title if hasattr(j, "title") and j.title else path.stem)
            at = j.account_type if hasattr(j, "account_type") and j.account_type else AccountType.OTHER
            # Display name mimics the real pipeline: original filename wins over
            # the possibly-converted Docling path (e.g. HEIC → JPEG).
            display_name = (
                j.original_filename if hasattr(j, "original_filename") and j.original_filename
                else path.name
            )
            # Clone per file — tests reuse the same TransactionRow across entries,
            # and stamping metadata in-place would otherwise overwrite siblings.
            stamped = [
                t.model_copy(update={
                    "StatementTitle": title, "AccountType": at, "source_file": display_name,
                })
                for t in txns
            ]
            results.append(pipeline_module.PdfExtractionResult(
                pdf_path=path, parser_name=parser, transactions=stamped,
                title=title, account_type=at, display_name=display_name, error=err,
            ))
            all_txns.extend(stamped)
        df = pipeline_module.transactions_to_dataframe(all_txns, include_source=include_source)
        if dedupe and not df.empty:
            df = df.drop_duplicates(
                subset=["StatementTitle", "Date", "Amount", "Description"]
            ).reset_index(drop=True)
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

    # Per-file — now carries title + account_type
    assert body["files"] == [{
        "filename": "a.pdf",
        "title": "a",
        "account_type": "other",
        "parser": "scotiabank_passport_visa",
        "rows": 2,
        "error": None,
    }]

    # Rows — canonical 8-column schema (StatementTitle + AccountType + 6 others),
    # no source columns by default.
    assert set(body["rows"][0].keys()) == {
        "StatementTitle", "AccountType",
        "Date", "Amount", "Payee", "Description", "Reference", "CheckNumber",
    }
    assert body["rows"][0]["Date"] == "2025-03-27"
    assert body["rows"][0]["Amount"] == "-4.25"
    assert body["rows"][0]["StatementTitle"] == "a"
    assert body["rows"][0]["AccountType"] == "other"


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
    assert lines[0] == (
        "StatementTitle,AccountType,Date,Amount,Payee,Description,Reference,CheckNumber"
    )
    # StatementTitle "a" (from filename stem), AccountType "other" (no text auto-detect).
    assert lines[1].startswith("a,other,2025-03-27,-4.25,STARBUCKS")


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
        "StatementTitle", "AccountType",
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


def test_extract_dedupes_within_same_statement_title(client: TestClient, stub_pipeline) -> None:
    # Two uploads with the *same* title (e.g. overlapping statement windows for
    # the same account) should dedupe identical rows. Dedup key is
    # (StatementTitle, Date, Amount, Description), so same-title-same-row -> one row.
    dup = _txn("2025-03-27", "-4.25", "STARBUCKS")
    stub_pipeline({
        "a.pdf": ("scotiabank_passport_visa", [dup], None),
        "b.pdf": ("scotiabank_passport_visa", [dup], None),
    })
    r = client.post(
        "/extract",
        data={
            "titles": ["March Visa", "March Visa"],
            "account_types": ["visa", "visa"],
        },
        files=[
            ("files", ("a.pdf", _pdf_bytes(), "application/pdf")),
            ("files", ("b.pdf", _pdf_bytes(), "application/pdf")),
        ],
    )
    body = r.json()
    assert body["summary"]["rows_extracted"] == 2
    assert body["summary"]["rows_after_dedup"] == 1
    assert len(body["rows"]) == 1


def test_extract_keeps_same_row_across_distinct_statements(client: TestClient, stub_pipeline) -> None:
    # Different titles -> the same-looking row is legitimately a distinct
    # transaction on a different statement, and must be preserved.
    dup = _txn("2025-03-27", "-4.25", "STARBUCKS")
    stub_pipeline({
        "a.pdf": ("scotiabank_passport_visa", [dup], None),
        "b.pdf": ("scotiabank_passport_visa", [dup], None),
    })
    r = client.post(
        "/extract",
        data={"titles": ["March Visa", "March Amex"]},
        files=[
            ("files", ("a.pdf", _pdf_bytes(), "application/pdf")),
            ("files", ("b.pdf", _pdf_bytes(), "application/pdf")),
        ],
    )
    body = r.json()
    assert body["summary"]["rows_after_dedup"] == 2
    assert {r_["StatementTitle"] for r_ in body["rows"]} == {"March Visa", "March Amex"}


# ---------------------------------------------------------------------------
# /extract — error paths
# ---------------------------------------------------------------------------

def test_extract_accepts_heic_and_converts_to_jpeg(
    client: TestClient, stub_pipeline, tmp_path,
) -> None:
    # Generate a real HEIF byte stream so the conversion path actually runs.
    pillow_heif = pytest.importorskip("pillow_heif")
    from PIL import Image

    pillow_heif.register_heif_opener()
    heic_path = tmp_path / "fixture.heic"
    Image.new("RGB", (32, 32), color=(100, 200, 150)).save(heic_path, format="HEIF")
    heic_bytes = heic_path.read_bytes()

    # The pipeline is stubbed; what matters is that ingress accepts HEIC,
    # converts it, and hands the resulting path to the (faked) pipeline. The
    # fake maps by filename.stem-matching, and the converted file is named
    # "phone.jpg" (stem preserved). We key on that.
    stub_pipeline({
        "phone.jpg": ("generic_table", [_txn("2025-03-27", "-4.25", "RECEIPT")], None),
    })

    r = client.post(
        "/extract",
        files=[("files", ("phone.heic", heic_bytes, "image/heic"))],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # source_file should show the user's original HEIC name, not the JPEG.
    assert body["files"][0]["filename"] == "phone.heic"
    assert body["summary"]["rows_after_dedup"] == 1


def test_extract_accepts_jpg_and_png(client: TestClient, stub_pipeline) -> None:
    # Build real (small) images so ingest.normalize_for_docling's size check
    # can decode them. Tiny dimensions → passthrough (under the 2500 px cap).
    from PIL import Image as _Image
    jpg_buf = io.BytesIO()
    _Image.new("RGB", (400, 300), (240, 240, 240)).save(jpg_buf, format="JPEG")
    png_buf = io.BytesIO()
    _Image.new("RGB", (400, 300), (200, 200, 200)).save(png_buf, format="PNG")

    stub_pipeline({
        "photo.jpg": ("generic_table", [_txn("2025-03-27", "-4.25", "STARBUCKS")], None),
        "scan.png": ("generic_table", [_txn("2025-03-28", "-60.00", "SHELL")], None),
    })
    r = client.post(
        "/extract",
        files=[
            ("files", ("photo.jpg", jpg_buf.getvalue(), "image/jpeg")),
            ("files", ("scan.png", png_buf.getvalue(), "image/png")),
        ],
    )
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["rows_after_dedup"] == 2


def test_extract_rejects_unsupported_type(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({})
    r = client.post(
        "/extract",
        files=[("files", ("notes.docx", b"whatever", "application/vnd.ms-word"))],
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "Unsupported file type" in detail
    assert "Accepted" in detail


def test_extract_rejects_empty_file(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({})
    r = client.post(
        "/extract",
        files=[("files", ("a.pdf", b"", "application/pdf"))],
    )
    assert r.status_code == 400
    assert "empty" in r.json()["detail"]


def test_extract_rejects_mismatched_titles_length(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({})
    r = client.post(
        "/extract",
        data={"titles": ["only-one"]},
        files=[
            ("files", ("a.pdf", _pdf_bytes(), "application/pdf")),
            ("files", ("b.pdf", _pdf_bytes(), "application/pdf")),
        ],
    )
    assert r.status_code == 400
    assert "titles has" in r.json()["detail"]


def test_extract_rejects_unknown_account_type(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({})
    r = client.post(
        "/extract",
        data={"account_types": ["platinum"]},  # not a known AccountType value
        files=[("files", ("a.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert r.status_code == 400
    assert "Unknown account_type" in r.json()["detail"]


def test_extract_accepts_explicit_account_type(client: TestClient, stub_pipeline) -> None:
    stub_pipeline({
        "a.pdf": ("scotiabank_passport_visa", [
            _txn("2025-03-27", "-4.25", "STARBUCKS"),
        ], None),
    })
    r = client.post(
        "/extract",
        data={"titles": ["March Amex"], "account_types": ["amex"]},
        files=[("files", ("a.pdf", _pdf_bytes(), "application/pdf"))],
    )
    body = r.json()
    assert body["files"][0]["title"] == "March Amex"
    assert body["files"][0]["account_type"] == "amex"
    assert body["rows"][0]["StatementTitle"] == "March Amex"
    assert body["rows"][0]["AccountType"] == "amex"
    assert body["summary"]["by_account_type"] == {"amex": 1}


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
