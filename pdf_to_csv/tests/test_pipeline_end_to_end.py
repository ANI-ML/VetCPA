"""End-to-end pipeline tests: parser selection, dedup, DataFrame shape, batch
aggregation, and graceful error handling."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from pdf_to_csv import pipeline as pipeline_module
from pdf_to_csv.account_type import AccountType
from pdf_to_csv.docling_client import ExtractedTable, ParsedPDF
from pdf_to_csv.models import TransactionRow
from pdf_to_csv.pipeline import (
    PARSER_REGISTRY,
    NoParserMatchedError,
    detect_bank_parser,
    extract_transactions_from_many,
    extract_transactions_from_pdf,
    transactions_to_dataframe,
)


# ---------------------------------------------------------------------------
# Registry ordering + parser selection
# ---------------------------------------------------------------------------

def test_registry_has_scotiabank_before_generic() -> None:
    names = [p.name for p in PARSER_REGISTRY]
    assert "scotiabank_passport_visa" in names
    assert "generic_table" in names
    # Specific parsers must take precedence over the generic fallback.
    assert names.index("scotiabank_passport_visa") < names.index("generic_table")


def test_detect_bank_parser_picks_scotiabank_when_it_matches() -> None:
    # Minimal ParsedPDF that Scotiabank will match: has "scotiabank" in text AND
    # a transaction-table-shaped header.
    table = ExtractedTable(
        page_number=1,
        headers=["REF.#", "TRANS. DATE", "POST DATE", "DETAILS", "AMOUNT($)"],
        rows=[["001", "Mar 27", "Mar 28", "STARBUCKS", "4.25"]],
        dataframe=pd.DataFrame(
            [["001", "Mar 27", "Mar 28", "STARBUCKS", "4.25"]],
            columns=["REF.#", "TRANS. DATE", "POST DATE", "DETAILS", "AMOUNT($)"],
        ),
    )
    parsed = ParsedPDF(
        tables=[table],
        text="Scotiabank Passport\nStatement Period Mar 28, 2025 - Apr 27, 2025",
    )
    assert detect_bank_parser(parsed).name == "scotiabank_passport_visa"


def test_detect_bank_parser_falls_back_to_generic_for_unknown_bank() -> None:
    # An unknown-bank table: Scotiabank won't match (no "scotiabank" keyword),
    # but the generic parser always claims a PDF with tables.
    table = ExtractedTable(
        page_number=1,
        headers=["Date", "Description", "Amount"],
        rows=[
            ["2025-03-27", "STARBUCKS", "-4.25"],
            ["2025-03-28", "SHELL", "-60.00"],
        ],
        dataframe=pd.DataFrame(
            [["2025-03-27", "STARBUCKS", "-4.25"], ["2025-03-28", "SHELL", "-60.00"]],
            columns=["Date", "Description", "Amount"],
        ),
    )
    parsed = ParsedPDF(tables=[table], text="FakeBank Statement")
    assert detect_bank_parser(parsed).name == "generic_table"


def test_detect_bank_parser_raises_when_nothing_matches() -> None:
    parsed = ParsedPDF(tables=[], text="not a statement")
    with pytest.raises(NoParserMatchedError):
        detect_bank_parser(parsed)


# ---------------------------------------------------------------------------
# DataFrame shape + column order
# ---------------------------------------------------------------------------

def test_transactions_to_dataframe_respects_canonical_column_order() -> None:
    txns = [
        TransactionRow(
            StatementTitle="March Visa",
            AccountType=AccountType.VISA,
            Date=date(2025, 3, 27),
            Amount=Decimal("-4.25"),
            Payee="STARBUCKS",
            Description="STARBUCKS TORONTO ON",
            Reference="001",
            CheckNumber="",
        ),
    ]
    df = transactions_to_dataframe(txns)
    assert list(df.columns) == [
        "StatementTitle",
        "AccountType",
        "Date",
        "Amount",
        "Payee",
        "Description",
        "Reference",
        "CheckNumber",
    ]
    assert df.iloc[0]["StatementTitle"] == "March Visa"
    assert df.iloc[0]["AccountType"] == "visa"
    assert df.iloc[0]["Date"] == "2025-03-27"
    assert df.iloc[0]["Amount"] == "-4.25"


def test_transactions_to_dataframe_empty_input_returns_empty_canonical_shape() -> None:
    df = transactions_to_dataframe([])
    assert df.empty
    assert list(df.columns) == [
        "StatementTitle", "AccountType",
        "Date", "Amount", "Payee", "Description", "Reference", "CheckNumber",
    ]


def test_transactions_to_dataframe_sorts_rows_by_statement_then_date() -> None:
    # Interleave two statements; output should group them by StatementTitle
    # and then sort by Date within each group.
    txns = [
        _make_txn("March Visa", AccountType.VISA, "2025-03-28", "-60.00", "SHELL"),
        _make_txn("March Amex", AccountType.AMEX, "2025-03-27", "-9.99", "SPOTIFY"),
        _make_txn("March Visa", AccountType.VISA, "2025-03-27", "-4.25", "STARBUCKS"),
        _make_txn("March Amex", AccountType.AMEX, "2025-03-28", "-12.00", "NETFLIX"),
    ]
    df = transactions_to_dataframe(txns)
    assert df["StatementTitle"].tolist() == [
        "March Amex", "March Amex", "March Visa", "March Visa",
    ]
    # Within each title, dates should be ascending.
    amex_dates = df[df["StatementTitle"] == "March Amex"]["Date"].tolist()
    visa_dates = df[df["StatementTitle"] == "March Visa"]["Date"].tolist()
    assert amex_dates == sorted(amex_dates)
    assert visa_dates == sorted(visa_dates)


def _make_txn(title, account_type, d, amt, desc):
    y, m, dd = (int(x) for x in d.split("-"))
    return TransactionRow(
        StatementTitle=title, AccountType=account_type,
        Date=date(y, m, dd), Amount=Decimal(amt),
        Payee=desc.split()[0], Description=desc,
        Reference="", CheckNumber="",
    )


def test_transactions_to_dataframe_with_source_adds_audit_columns() -> None:
    txns = [
        TransactionRow(
            Date=date(2025, 3, 27), Amount=Decimal("-4.25"),
            Payee="STARBUCKS", Description="STARBUCKS",
            Reference="", CheckNumber="",
            source_bank="scotiabank_passport_visa", source_file="statement.pdf",
        ),
    ]
    df = transactions_to_dataframe(txns, include_source=True)
    assert "source_bank" in df.columns
    assert "source_file" in df.columns
    assert df.iloc[0]["source_bank"] == "scotiabank_passport_visa"


# ---------------------------------------------------------------------------
# Pipeline batch + dedup
# ---------------------------------------------------------------------------

def _patch_fake_pipeline(monkeypatch: pytest.MonkeyPatch, by_path: dict[str, list[TransactionRow]]) -> None:
    """Bypass Docling + parsers entirely by stubbing extract_transactions_from_pdf."""
    def fake(pdf_path, **kwargs):
        key = str(pdf_path)
        return pipeline_module.PdfExtractionResult(
            pdf_path=Path(pdf_path),
            parser_name="scotiabank_passport_visa",
            transactions=by_path.get(key, []),
        )
    monkeypatch.setattr(pipeline_module, "extract_transactions_from_pdf", fake)


def _txn(d: str, amt: str, desc: str, *, src: str = "scotiabank_passport_visa") -> TransactionRow:
    y, m, dd = [int(x) for x in d.split("-")]
    return TransactionRow(
        Date=date(y, m, dd),
        Amount=Decimal(amt),
        Payee=desc.split()[0],
        Description=desc,
        Reference="",
        CheckNumber="",
        source_bank=src,
    )


def test_extract_from_many_aggregates_and_dedupes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    pdf_a.write_bytes(b"")
    pdf_b.write_bytes(b"")

    # b.pdf overlaps with a.pdf on one transaction — dedup should collapse it.
    _patch_fake_pipeline(monkeypatch, {
        str(pdf_a): [
            _txn("2025-03-27", "-4.25", "STARBUCKS TORONTO ON"),
            _txn("2025-03-28", "-60.00", "SHELL TORONTO ON"),
        ],
        str(pdf_b): [
            _txn("2025-03-28", "-60.00", "SHELL TORONTO ON"),   # duplicate
            _txn("2025-03-29", "-12.00", "LOBLAWS TORONTO ON"),
        ],
    })

    df, results = extract_transactions_from_many([pdf_a, pdf_b], dedupe=True)

    assert len(df) == 3
    assert sorted(df["Date"].tolist()) == ["2025-03-27", "2025-03-28", "2025-03-29"]
    assert [r.parser_name for r in results] == [
        "scotiabank_passport_visa", "scotiabank_passport_visa",
    ]


def test_extract_from_many_dedupe_off_keeps_duplicates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"")
    _patch_fake_pipeline(monkeypatch, {
        str(pdf): [
            _txn("2025-03-27", "-4.25", "STARBUCKS"),
            _txn("2025-03-27", "-4.25", "STARBUCKS"),
        ],
    })
    df, _ = extract_transactions_from_many([pdf], dedupe=False)
    assert len(df) == 2


def test_pdfjob_from_any_normalizes_paths() -> None:
    from pdf_to_csv.pipeline import PdfJob
    p = Path("/tmp/x.pdf")
    assert PdfJob.from_any(p).path == p
    assert PdfJob.from_any("/tmp/y.pdf").path == Path("/tmp/y.pdf")
    existing = PdfJob(path=p, title="custom", account_type=AccountType.AMEX)
    assert PdfJob.from_any(existing) is existing  # passthrough


def test_extract_from_pdf_uses_provided_title_and_account_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User-provided metadata must win over filename-stem / auto-detect."""
    from pdf_to_csv.pipeline import PdfJob, extract_transactions_from_pdf
    pdf = tmp_path / "random_filename.pdf"
    pdf.write_bytes(b"")

    # Stub Docling + parser to always return one row.
    monkeypatch.setattr(
        pipeline_module, "parse_pdf",
        lambda path, **kw: ParsedPDF(tables=[], text="This is a Scotiabank Visa card"),
    )
    class FakeParser:
        name = "fake_bank"
        def is_match(self, parsed): return True
        def extract_transactions(self, parsed):
            return [_txn("2025-03-27", "-4.25", "STARBUCKS")]
    monkeypatch.setattr(pipeline_module, "PARSER_REGISTRY", [FakeParser()])

    # Override: title="March Amex", account_type=AMEX. Even though the text
    # says "Visa", the explicit override must win.
    result = extract_transactions_from_pdf(
        PdfJob(path=pdf, title="March Amex", account_type=AccountType.AMEX)
    )
    assert result.title == "March Amex"
    assert result.account_type == AccountType.AMEX
    assert result.transactions[0].StatementTitle == "March Amex"
    assert result.transactions[0].AccountType in (AccountType.AMEX, "amex")


def test_extract_from_pdf_auto_detects_account_type_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare Path → title defaults to stem, account_type auto-detected from text."""
    from pdf_to_csv.pipeline import extract_transactions_from_pdf
    pdf = tmp_path / "scotiabank_april.pdf"
    pdf.write_bytes(b"")

    monkeypatch.setattr(
        pipeline_module, "parse_pdf",
        lambda path, **kw: ParsedPDF(tables=[], text="Scotiabank Passport Visa Infinite"),
    )
    class FakeParser:
        name = "fake_bank"
        def is_match(self, parsed): return True
        def extract_transactions(self, parsed):
            return [_txn("2025-03-27", "-4.25", "STARBUCKS")]
    monkeypatch.setattr(pipeline_module, "PARSER_REGISTRY", [FakeParser()])

    result = extract_transactions_from_pdf(pdf)
    assert result.title == "scotiabank_april"  # filename stem
    assert result.account_type == AccountType.VISA  # auto-detected


def test_extract_from_pdf_surfaces_docling_error_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"")

    def exploding_parse_pdf(path, **kwargs):
        raise RuntimeError("docling kaboom")

    monkeypatch.setattr(pipeline_module, "parse_pdf", exploding_parse_pdf)
    result = extract_transactions_from_pdf(pdf)
    assert result.transactions == []
    assert result.error is not None
    assert "docling" in result.error


def test_extract_from_pdf_surfaces_parser_error_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"")

    # Pipeline returns a valid ParsedPDF, but the chosen parser raises.
    class FakeParser:
        name = "fake"

        def is_match(self, parsed):
            return True

        def extract_transactions(self, parsed):
            raise ValueError("bad data")

    monkeypatch.setattr(
        pipeline_module,
        "parse_pdf",
        lambda p, **kw: ParsedPDF(tables=[], text=""),
    )
    monkeypatch.setattr(pipeline_module, "PARSER_REGISTRY", [FakeParser()])
    result = extract_transactions_from_pdf(pdf)
    assert result.parser_name == "fake"
    assert result.error is not None
    assert "bad data" in result.error
