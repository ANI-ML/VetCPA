"""Pipeline orchestration.

Turns PDFs into TransactionRow objects (and ultimately a DataFrame in the
canonical schema). The parser registry is an *ordered* list: bank-specific
parsers get first shot at each PDF, and `GenericTableParser` is always last so
unknown banks still produce a workable CSV.

Adding a new bank:
    1. Create `parsers/<bank>.py` with a `BaseParser` subclass.
    2. Append an instance to `PARSER_REGISTRY` below (before GenericTableParser).
    3. Add tests under `tests/test_<bank>_parser.py`.

There is deliberately no plug-in autodiscovery or entry-points magic — the
registry is a plain Python list you read top-to-bottom to see which parsers
exist and in what precedence.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from pdf_to_csv.config import load_settings
from pdf_to_csv.docling_client import ParsedPDF, parse_pdf
from pdf_to_csv.models import TransactionRow
from pdf_to_csv.parsers.base_parser import BaseParser
from pdf_to_csv.parsers.generic_table import GenericTableParser
from pdf_to_csv.parsers.scotiabank_passport_visa import ScotiabankPassportVisaParser

if TYPE_CHECKING:  # pragma: no cover
    from docling.document_converter import DocumentConverter


# Order matters: first parser whose `is_match()` returns True wins. Put
# high-fidelity bank-specific parsers at the top; keep `GenericTableParser`
# last as the universal fallback.
PARSER_REGISTRY: list[BaseParser] = [
    ScotiabankPassportVisaParser(),
    GenericTableParser(),
]


class NoParserMatchedError(RuntimeError):
    """Raised when not even the generic fallback can handle a PDF."""


def detect_bank_parser(parsed: ParsedPDF) -> BaseParser:
    """Return the first registered parser whose `is_match` claims this PDF."""
    for parser in PARSER_REGISTRY:
        if parser.is_match(parsed):
            return parser
    raise NoParserMatchedError("No parser matched — not even the generic fallback.")


@dataclass
class PdfExtractionResult:
    """Result of extracting a single PDF. `error` is populated if extraction
    failed; callers can aggregate successes and failures into a summary."""

    pdf_path: Path
    parser_name: str | None
    transactions: list[TransactionRow]
    error: str | None = None


def extract_transactions_from_pdf(
    pdf_path: Path | str,
    *,
    do_ocr: bool = False,
    converter: "DocumentConverter | None" = None,
) -> PdfExtractionResult:
    """Run Docling + parser pipeline on one PDF. Never raises — failures land
    in `result.error` so batch runs don't die on a single bad file."""
    pdf_path = Path(pdf_path)
    try:
        parsed = parse_pdf(pdf_path, do_ocr=do_ocr, converter=converter)
    except Exception as exc:  # noqa: BLE001 - we want to report every failure mode
        return PdfExtractionResult(pdf_path, None, [], error=f"docling: {exc}")

    try:
        parser = detect_bank_parser(parsed)
    except NoParserMatchedError as exc:
        return PdfExtractionResult(pdf_path, None, [], error=str(exc))

    try:
        txns = parser.extract_transactions(parsed)
    except Exception as exc:  # noqa: BLE001
        return PdfExtractionResult(pdf_path, parser.name, [], error=f"{parser.name}: {exc}")

    # Stamp every row with its source file for downstream audit / debugging.
    for t in txns:
        t.source_file = pdf_path.name

    return PdfExtractionResult(pdf_path, parser.name, txns)


def transactions_to_dataframe(
    txns: list[TransactionRow], *, include_source: bool = False
) -> pd.DataFrame:
    """Canonical 6-column DataFrame (+ optional `source_bank`, `source_file`)."""
    settings = load_settings()
    if not txns:
        cols = list(settings.output_columns)
        if include_source:
            cols += ["source_bank", "source_file"]
        return pd.DataFrame(columns=cols)

    records = []
    for t in txns:
        record = {
            "Date": t.Date.isoformat(),
            "Amount": str(t.Amount),
            "Payee": t.Payee,
            "Description": t.Description,
            "Reference": t.Reference,
            "CheckNumber": t.CheckNumber,
        }
        if include_source:
            record["source_bank"] = t.source_bank or ""
            record["source_file"] = t.source_file or ""
        records.append(record)
    df = pd.DataFrame.from_records(records)
    # Enforce column order.
    cols = list(settings.output_columns)
    if include_source:
        cols += ["source_bank", "source_file"]
    return df[cols]


def _dedupe_key(row: dict[str, str]) -> tuple[str, str, str]:
    # Dedup key: (Date, Amount, Description). Two statements overlapping on
    # the same window shouldn't duplicate rows — this is the combination banks
    # are effectively unique on.
    return (row["Date"], row["Amount"], row["Description"])


def extract_transactions_from_many(
    pdf_paths: list[Path | str],
    *,
    dedupe: bool = True,
    include_source: bool = False,
    do_ocr: bool = False,
    converter: "DocumentConverter | None" = None,
) -> tuple[pd.DataFrame, list[PdfExtractionResult]]:
    """Batch entry point.

    Returns:
        (dataframe, per-file results). The DataFrame is the deduped canonical
        schema (optionally with source columns). The per-file results include
        parser names and any errors, for summary reporting in the CLI/API.
    """
    per_file: list[PdfExtractionResult] = []
    all_txns: list[TransactionRow] = []
    for p in pdf_paths:
        r = extract_transactions_from_pdf(p, do_ocr=do_ocr, converter=converter)
        per_file.append(r)
        all_txns.extend(r.transactions)

    df = transactions_to_dataframe(all_txns, include_source=include_source)
    if dedupe and not df.empty:
        # pandas drop_duplicates keeps the first occurrence — preserves parser
        # ordering (a bank-specific row keeps priority over a generic one if
        # both pipelines picked up the same transaction).
        df = df.drop_duplicates(subset=["Date", "Amount", "Description"]).reset_index(drop=True)
    return df, per_file
