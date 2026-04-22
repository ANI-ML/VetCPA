"""Tests for the `pdf-to-csv extract` CLI command.

We patch `extract_transactions_from_many` and `build_converter` so no Docling
work actually runs — these are CLI-shape tests, not pipeline tests (those live
in test_pipeline_end_to_end.py).
"""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from pdf_to_csv import pipeline as pipeline_module
from pdf_to_csv.cli import app
from pdf_to_csv.models import TransactionRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _txn(d: str, amount: str, payee: str, desc: str, source: str = "scotiabank_passport_visa") -> TransactionRow:
    y, m, dd = [int(x) for x in d.split("-")]
    return TransactionRow(
        Date=date(y, m, dd),
        Amount=Decimal(amount),
        Payee=payee,
        Description=desc,
        Reference="",
        CheckNumber="",
        source_bank=source,
    )


def _fake_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    per_pdf: dict[str, tuple[str | None, list[TransactionRow], str | None]],
) -> None:
    """Stub out Docling + pipeline. `per_pdf` maps pdf_path -> (parser, txns, error).

    The CLI re-imports these symbols inside the function body (lazy imports), so
    patches must target the *source* modules, not the CLI module.
    """
    monkeypatch.setattr(
        "pdf_to_csv.docling_client.build_converter",
        lambda **_: object(),
    )

    def fake_extract_many(jobs, *, dedupe, include_source, converter, do_ocr=False):
        results = []
        all_txns: list[TransactionRow] = []
        for j in jobs:
            # CLI calls with list[Path]; pipeline calls with list[PdfJob]. Unwrap either.
            p = j.path if hasattr(j, "path") else Path(j)
            title = j.title if hasattr(j, "title") and j.title else p.stem
            parser, txns, err = per_pdf[str(p)]
            # Mirror the real pipeline: stamp statement metadata + source file.
            for t in txns:
                t.StatementTitle = title
                t.source_file = p.name
            results.append(pipeline_module.PdfExtractionResult(
                pdf_path=p, parser_name=parser, transactions=txns,
                title=title, error=err,
            ))
            all_txns.extend(txns)
        df = pipeline_module.transactions_to_dataframe(all_txns, include_source=include_source)
        if dedupe and not df.empty:
            df = df.drop_duplicates(
                subset=["StatementTitle", "Date", "Amount", "Description"]
            ).reset_index(drop=True)
        return df, results

    monkeypatch.setattr(
        "pdf_to_csv.pipeline.extract_transactions_from_many",
        fake_extract_many,
    )


@pytest.fixture()
def fake_pdfs(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"%PDF-1.4\n")
    b.write_bytes(b"%PDF-1.4\n")
    return a, b


# ---------------------------------------------------------------------------
# Happy path: writes CSV, prints per-PDF + summary
# ---------------------------------------------------------------------------

def test_extract_writes_csv_with_canonical_schema(monkeypatch: pytest.MonkeyPatch, fake_pdfs, tmp_path: Path) -> None:
    a, b = fake_pdfs
    _fake_pipeline(monkeypatch, {
        str(a): ("scotiabank_passport_visa", [
            _txn("2025-03-27", "-4.25", "STARBUCKS", "STARBUCKS TORONTO ON"),
            _txn("2025-03-28", "-60.00", "SHELL", "SHELL TORONTO ON"),
        ], None),
        str(b): ("scotiabank_passport_visa", [
            _txn("2025-03-29", "-12.00", "LOBLAWS", "LOBLAWS TORONTO ON"),
        ], None),
    })

    out = tmp_path / "out.csv"
    result = CliRunner().invoke(app, [
        "extract", str(a), str(b), "--out", str(out),
    ])

    assert result.exit_code == 0, result.output
    assert out.exists()

    with out.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    # Canonical column order — StatementTitle + AccountType lead for readability.
    assert list(rows[0].keys()) == [
        "StatementTitle", "AccountType",
        "Date", "Amount", "Payee", "Description", "Reference", "CheckNumber",
    ]
    # Rows sorted by (StatementTitle, Date): title "a" before "b".
    assert rows[0]["StatementTitle"] == "a"
    assert rows[0]["Date"] == "2025-03-27"
    assert rows[0]["Amount"] == "-4.25"
    assert rows[0]["Payee"] == "STARBUCKS"

    # Per-PDF progress line for each file.
    assert "a.pdf: 2 rows (scotiabank_passport_visa)" in result.output
    assert "b.pdf: 1 rows (scotiabank_passport_visa)" in result.output
    # Summary shows parser breakdown.
    assert "scotiabank_passport_visa: 3" in result.output
    assert "PDFs processed : 2" in result.output


# ---------------------------------------------------------------------------
# Excel output
# ---------------------------------------------------------------------------

def test_extract_writes_excel_when_requested(monkeypatch: pytest.MonkeyPatch, fake_pdfs, tmp_path: Path) -> None:
    a, _ = fake_pdfs
    _fake_pipeline(monkeypatch, {
        str(a): ("scotiabank_passport_visa", [
            _txn("2025-03-27", "-4.25", "STARBUCKS", "STARBUCKS"),
        ], None),
        str(fake_pdfs[1]): ("scotiabank_passport_visa", [], None),
    })

    out = tmp_path / "out.csv"
    xlsx = tmp_path / "out.xlsx"
    result = CliRunner().invoke(app, [
        "extract", str(a), "--out", str(out), "--excel", str(xlsx),
    ])

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert xlsx.exists()

    xdf = pd.read_excel(xlsx, engine="openpyxl")
    assert list(xdf.columns) == [
        "StatementTitle", "AccountType",
        "Date", "Amount", "Payee", "Description", "Reference", "CheckNumber",
    ]
    assert len(xdf) == 1


# ---------------------------------------------------------------------------
# --include-source adds audit columns
# ---------------------------------------------------------------------------

def test_extract_include_source_adds_audit_columns(monkeypatch: pytest.MonkeyPatch, fake_pdfs, tmp_path: Path) -> None:
    a, _ = fake_pdfs
    _fake_pipeline(monkeypatch, {
        str(a): ("scotiabank_passport_visa", [
            _txn("2025-03-27", "-4.25", "STARBUCKS", "STARBUCKS"),
        ], None),
        str(fake_pdfs[1]): ("scotiabank_passport_visa", [], None),
    })

    out = tmp_path / "out.csv"
    result = CliRunner().invoke(app, [
        "extract", str(a), "--out", str(out), "--include-source",
    ])

    assert result.exit_code == 0, result.output
    with out.open() as f:
        header = next(csv.reader(f))
    assert header == [
        "StatementTitle", "AccountType",
        "Date", "Amount", "Payee", "Description", "Reference", "CheckNumber",
        "source_bank", "source_file",
    ]


# ---------------------------------------------------------------------------
# Dedup summary: shows "X -> Y after dedup" only when counts differ
# ---------------------------------------------------------------------------

def test_extract_summary_shows_dedup_reduction(monkeypatch: pytest.MonkeyPatch, fake_pdfs, tmp_path: Path) -> None:
    a, b = fake_pdfs
    dup = _txn("2025-03-27", "-4.25", "STARBUCKS", "STARBUCKS")
    _fake_pipeline(monkeypatch, {
        str(a): ("scotiabank_passport_visa", [dup], None),
        str(b): ("scotiabank_passport_visa", [dup], None),  # same (Date, Amount, Description) → dedup'd
    })
    out = tmp_path / "out.csv"
    result = CliRunner().invoke(app, ["extract", str(a), str(b), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "2 → 1 after dedup" in result.output


def test_extract_summary_silent_when_no_dupes(monkeypatch: pytest.MonkeyPatch, fake_pdfs, tmp_path: Path) -> None:
    a, _ = fake_pdfs
    _fake_pipeline(monkeypatch, {
        str(a): ("scotiabank_passport_visa", [_txn("2025-03-27", "-4.25", "STARBUCKS", "STARBUCKS")], None),
        str(fake_pdfs[1]): ("scotiabank_passport_visa", [], None),
    })
    out = tmp_path / "out.csv"
    result = CliRunner().invoke(app, ["extract", str(a), "--out", str(out)])
    assert result.exit_code == 0
    assert "after dedup" not in result.output
    assert "Rows extracted : 1" in result.output


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def test_extract_surfaces_per_file_errors_and_exits_nonzero(monkeypatch: pytest.MonkeyPatch, fake_pdfs, tmp_path: Path) -> None:
    a, b = fake_pdfs
    _fake_pipeline(monkeypatch, {
        str(a): ("scotiabank_passport_visa", [
            _txn("2025-03-27", "-4.25", "STARBUCKS", "STARBUCKS"),
        ], None),
        str(b): (None, [], "docling: kaboom"),
    })
    out = tmp_path / "out.csv"
    result = CliRunner().invoke(app, ["extract", str(a), str(b), "--out", str(out)])
    # Non-zero exit so shell scripts can branch on it.
    assert result.exit_code == 1, result.output
    # The CSV for the successful PDF still gets written.
    assert out.exists()
    assert "b.pdf: FAILED — docling: kaboom" in result.output
    assert "PDFs processed : 2 (1 failed)" in result.output


def test_extract_help_is_usable_without_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    # --help must not import Docling or the pipeline; guards the lazy-import boundary.
    result = CliRunner().invoke(app, ["extract", "--help"])
    assert result.exit_code == 0
    assert "Extract transactions" in result.output
    assert "--excel" in result.output
    assert "--include-source" in result.output
