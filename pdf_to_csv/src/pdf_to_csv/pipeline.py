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

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Union

import pandas as pd

from pdf_to_csv.account_type import AccountType, detect_account_type
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


@dataclass
class PdfJob:
    """One input document to process, plus the metadata the accountant will see
    in the output CSV.

    * `path` points at whatever Docling will actually read — after any
      preprocessing (e.g. a HEIC file gets converted to JPEG and `path` points
      at the JPEG).
    * `original_filename` carries the user-facing name so logs, error messages,
      and the `source_file` column show "photo.heic" even though Docling saw
      "photo.jpg". Defaults to `path.name` when unset.
    * `title` defaults to the original filename's stem when unset.
    * `account_type` is auto-detected from the document text when unset.
    """

    path: Path
    title: str | None = None
    account_type: AccountType | None = None
    original_filename: str | None = None

    @property
    def display_name(self) -> str:
        return self.original_filename or self.path.name

    @property
    def default_title(self) -> str:
        return Path(self.display_name).stem

    @classmethod
    def from_any(cls, value: "PdfJob | Path | str") -> "PdfJob":
        if isinstance(value, PdfJob):
            return value
        return cls(path=Path(value))


# Callers can pass any of these; we normalize to PdfJob internally.
PdfJobLike = Union[PdfJob, Path, str]


def detect_bank_parser(parsed: ParsedPDF) -> BaseParser:
    """Return the first registered parser whose `is_match` claims this PDF."""
    for parser in PARSER_REGISTRY:
        if parser.is_match(parsed):
            return parser
    raise NoParserMatchedError("No parser matched — not even the generic fallback.")


@dataclass
class PdfExtractionResult:
    """Result of extracting a single document. `error` is populated if extraction
    failed; callers can aggregate successes and failures into a summary.

    `pdf_path` is the path Docling actually read — which may be a JPEG we
    generated from a HEIC. `display_name` is the user-facing filename; API and
    CLI summaries should prefer this over `pdf_path.name` so HEIC uploads
    display as the original .heic.
    """

    pdf_path: Path
    parser_name: str | None
    transactions: list[TransactionRow]
    title: str = ""
    account_type: AccountType = AccountType.OTHER
    display_name: str = ""
    error: str | None = None


def extract_transactions_from_pdf(
    job: PdfJobLike,
    *,
    do_ocr: bool = False,
    converter: "DocumentConverter | None" = None,
) -> PdfExtractionResult:
    """Run Docling + parser pipeline on one PDF. Never raises — failures land
    in `result.error` so batch runs don't die on a single bad file."""
    job = PdfJob.from_any(job)
    pdf_path = job.path
    display_name = job.display_name
    default_title = job.default_title
    _base_result_kw = {"pdf_path": pdf_path, "display_name": display_name}

    try:
        parsed = parse_pdf(pdf_path, do_ocr=do_ocr, converter=converter)
    except Exception as exc:  # noqa: BLE001 - we want to report every failure mode
        return PdfExtractionResult(
            **_base_result_kw,
            parser_name=None,
            transactions=[],
            title=job.title or default_title,
            account_type=job.account_type or AccountType.OTHER,
            error=f"docling: {exc}",
        )

    # Resolve metadata: user-provided wins, else auto-detect.
    title = job.title if job.title is not None else default_title
    account_type = job.account_type if job.account_type is not None else detect_account_type(
        parsed.text or ""
    )

    try:
        parser = detect_bank_parser(parsed)
    except NoParserMatchedError as exc:
        return PdfExtractionResult(
            **_base_result_kw, parser_name=None, transactions=[],
            title=title, account_type=account_type, error=str(exc),
        )

    try:
        txns = parser.extract_transactions(parsed)
    except Exception as exc:  # noqa: BLE001
        return PdfExtractionResult(
            **_base_result_kw, parser_name=parser.name, transactions=[],
            title=title, account_type=account_type, error=f"{parser.name}: {exc}",
        )

    # Stamp every row with the resolved statement metadata. `source_file` uses
    # the display name so HEIC uploads don't show up as the converted JPEG.
    for t in txns:
        t.StatementTitle = title
        t.AccountType = account_type
        t.source_file = display_name

    return PdfExtractionResult(
        **_base_result_kw, parser_name=parser.name, transactions=txns,
        title=title, account_type=account_type,
    )


def transactions_to_dataframe(
    txns: list[TransactionRow], *, include_source: bool = False
) -> pd.DataFrame:
    """Canonical DataFrame (optionally + audit columns).

    Rows are sorted by (StatementTitle, Date) so output is grouped by
    statement — the shape the accountant reads most naturally.
    """
    settings = load_settings()
    cols = list(settings.output_columns)
    if include_source:
        cols += ["source_bank", "source_file"]

    if not txns:
        return pd.DataFrame(columns=cols)

    records = []
    for t in txns:
        record = {
            "StatementTitle": t.StatementTitle,
            "AccountType": _account_value(t.AccountType),
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

    df = pd.DataFrame.from_records(records)[cols]
    # Group by statement for the accountant; stable sort keeps original
    # within-statement ordering when dates tie.
    if not df.empty:
        df = df.sort_values(
            by=["StatementTitle", "Date"], kind="stable"
        ).reset_index(drop=True)
    return df


def _account_value(at: AccountType | str) -> str:
    """Pydantic v2 with use_enum_values=True stores the raw string; older
    callers might still pass an enum. Normalize either way."""
    return at.value if isinstance(at, AccountType) else str(at)


def extract_transactions_from_many(
    jobs: list[PdfJobLike],
    *,
    dedupe: bool = True,
    include_source: bool = False,
    do_ocr: bool = False,
    converter: "DocumentConverter | None" = None,
) -> tuple[pd.DataFrame, list[PdfExtractionResult]]:
    """Batch entry point.

    Accepts `PdfJob` objects with explicit title / account_type, or bare
    Path / str when you want defaults (title=stem, account_type=auto-detect).

    Returns:
        (dataframe, per-file results). The DataFrame is the deduped canonical
        schema (optionally with source columns), sorted by (StatementTitle, Date).
        Per-file results carry parser name, resolved metadata, and any errors.
    """
    per_file: list[PdfExtractionResult] = []
    all_txns: list[TransactionRow] = []
    for j in jobs:
        r = extract_transactions_from_pdf(j, do_ocr=do_ocr, converter=converter)
        per_file.append(r)
        all_txns.extend(r.transactions)

    df = transactions_to_dataframe(all_txns, include_source=include_source)
    if dedupe and not df.empty:
        # Dedup key: (StatementTitle, Date, Amount, Description). Including
        # StatementTitle means the same transaction appearing in two distinct
        # statements (different titles) stays — that's almost always intentional.
        df = df.drop_duplicates(
            subset=["StatementTitle", "Date", "Amount", "Description"]
        ).reset_index(drop=True)
    return df, per_file
