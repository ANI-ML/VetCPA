"""Thin wrapper around Docling's DocumentConverter.

Everything Docling-specific lives here. The rest of the codebase talks to this
module via `parse_pdf_to_tables()` + `ExtractedTable`, so swapping extractors
(or pinning to a new Docling major version) is a one-file change.

Docling imports are done lazily inside functions so that:
  * importing `pdf_to_csv.docling_client` stays cheap (fast CLI --help, fast tests),
  * and tests that don't exercise PDF parsing don't need Docling's heavy deps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docling.document_converter import DocumentConverter


@dataclass
class ExtractedTable:
    """A single table extracted from a PDF, in a parser-friendly shape.

    Attributes:
        page_number: 1-indexed page number if Docling reports it, else None.
        headers:     column headers as strings (from DataFrame columns).
        rows:        body rows as list-of-list of strings (no header row).
        dataframe:   the underlying pandas DataFrame (strings, no NaN).
        raw:         the original Docling TableItem, for parsers that need more.
    """

    page_number: int | None
    headers: list[str]
    rows: list[list[str]]
    dataframe: pd.DataFrame
    raw: Any = field(default=None, repr=False)

    @property
    def shape(self) -> tuple[int, int]:
        return self.dataframe.shape


@dataclass
class ParsedPDF:
    """Everything a parser needs from a single PDF: extracted tables + the full
    document text. Text is the markdown export — used by parsers to read header
    fields like "Statement Period ..." that don't live inside a table."""

    tables: list[ExtractedTable]
    text: str
    source_path: Path | None = None


def _page_number_of(table: Any) -> int | None:
    """Best-effort page number pulled from a Docling TableItem's provenance."""
    prov = getattr(table, "prov", None)
    if not prov:
        return None
    try:
        return int(prov[0].page_no)
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def _extracted_from_dataframe(
    df: pd.DataFrame,
    *,
    page_number: int | None,
    raw: Any = None,
) -> ExtractedTable:
    """Normalize a raw pandas DataFrame into an ExtractedTable (strings, no NaN)."""
    df = df.fillna("").astype(str)
    headers = [str(c) for c in df.columns]
    rows = df.values.tolist()
    return ExtractedTable(
        page_number=page_number,
        headers=headers,
        rows=rows,
        dataframe=df,
        raw=raw,
    )


def build_converter(*, do_ocr: bool = False) -> "DocumentConverter":
    """Construct a Docling DocumentConverter with OCR off by default.

    Rationale: bank/credit-card statements downloaded from online banking are
    digital-born PDFs, so OCR adds cost without benefit. Flip `do_ocr=True` for
    scanned/photographed statements.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


def parse_pdf_to_tables(
    pdf_path: Path | str,
    *,
    do_ocr: bool = False,
    converter: "DocumentConverter | None" = None,
) -> list[ExtractedTable]:
    """Run Docling on `pdf_path` and return every table it finds.

    Args:
        pdf_path:  path to a PDF file.
        do_ocr:    enable Docling OCR (default False).
        converter: reuse a pre-built DocumentConverter (saves startup cost when
                   parsing many PDFs in a batch — see `pipeline.py`).

    Raises:
        FileNotFoundError: if `pdf_path` does not exist.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    if converter is None:
        converter = build_converter(do_ocr=do_ocr)

    return parse_pdf(pdf_path, do_ocr=do_ocr, converter=converter).tables


def parse_pdf(
    pdf_path: Path | str,
    *,
    do_ocr: bool = False,
    converter: "DocumentConverter | None" = None,
) -> ParsedPDF:
    """Run Docling on `pdf_path` and return both tables and document text.

    Parsers use the `text` for header fields that don't live in a table
    (e.g. "Statement Period Mar 28, 2025 - Apr 27, 2025").
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    if converter is None:
        converter = build_converter(do_ocr=do_ocr)

    result = converter.convert(pdf_path)
    doc = result.document

    tables: list[ExtractedTable] = []
    for table in getattr(doc, "tables", None) or []:
        df = table.export_to_dataframe()
        tables.append(
            _extracted_from_dataframe(
                df, page_number=_page_number_of(table), raw=table
            )
        )

    try:
        text = doc.export_to_markdown()
    except Exception:  # pragma: no cover - defensive; some Docling builds lack markdown
        text = ""

    return ParsedPDF(tables=tables, text=text, source_path=pdf_path)
