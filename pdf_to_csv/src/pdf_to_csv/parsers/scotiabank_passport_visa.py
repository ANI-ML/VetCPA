"""Parser for Scotiabank Passport Visa (and Visa Infinite Business) statements.

Recognises the Transactions table whose headers are:
    REF.# | TRANS. DATE | POST DATE | DETAILS | AMOUNT($)

Design choices (all intentional, flag if they need to change):

* **Date field**: we use TRANS. DATE (when the transaction occurred), not POST DATE.
  Transaction date is what accountants want for books; posting date is a bank artifact.
* **Year inference**: row dates arrive as "Mar 27" (no year). We read
  `Statement Period Mmm DD, YYYY - Mmm DD, YYYY` from the PDF text and bind each
  row to the correct year, handling period-crossing-year-boundary cases.
* **Amount sign**: Scotiabank uses a trailing minus for credits/payments
  (e.g. `187.26-`). Debits (charges) are positive. We flip: credits become
  negative Decimals in the canonical schema.
* **FX sublines**: lines like `AMT 10.36 USD` appear directly under a transaction.
  Docling may surface them as (a) an embedded newline inside the DETAILS cell or
  (b) a standalone row with only DETAILS populated. We handle both by appending
  them to the preceding transaction's Description.
* **Non-transaction rows**: the cardholder banner (`MR X - 4538 XXXX XXXX 6019`)
  and subtotal rows (`SUB-TOTAL CREDITS/DEBITS`) are skipped. The 3-digit REF.#
  is the cleanest positive signal for a real transaction row.
* **Payee heuristic**: first chunk of DETAILS split on 2+ whitespace
  (`ANTHROPIC    ANTHROPIC.COMCA` -> payee = "ANTHROPIC"). The full DETAILS
  (including FX subline) is always preserved in Description.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from pdf_to_csv.docling_client import ExtractedTable, ParsedPDF
from pdf_to_csv.models import TransactionRow
from pdf_to_csv.parsers.base_parser import BaseParser


# ---------------------------------------------------------------------------
# Header / column detection
# ---------------------------------------------------------------------------

def _normalize_header(label: str) -> str:
    return re.sub(r"\s+", " ", label.upper().replace(".", "")).strip()


def is_transaction_table(headers: list[str]) -> bool:
    """True if `headers` look like a Scotiabank transactions table.

    Tolerant to whitespace, case, and punctuation variations. We require the
    four semantic columns (TRANS DATE, POST DATE, DETAILS, AMOUNT). REF is
    nice-to-have, not required — some Docling renderings drop the "#" header.
    """
    joined = " ".join(_normalize_header(h) for h in headers)
    required = ("TRANS", "POST", "DETAILS", "AMOUNT")
    return all(tok in joined for tok in required) and "DATE" in joined


def find_column_indices(headers: list[str]) -> dict[str, int]:
    """Map logical column names to their index in a row.

    Returns keys: "ref", "trans_date", "post_date", "details", "amount".
    Missing keys are simply omitted (caller decides whether that's fatal).
    """
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        norm = _normalize_header(raw)
        if "REF" in norm and "ref" not in mapping:
            mapping["ref"] = idx
        # Require TRANS/POST exclusivity so a merged "TRANS DATE POST DATE"
        # cell doesn't get aliased to both columns.
        if "TRANS" in norm and "DATE" in norm and "POST" not in norm:
            mapping["trans_date"] = idx
        if "POST" in norm and "DATE" in norm and "TRANS" not in norm:
            mapping["post_date"] = idx
        if "DETAIL" in norm:
            mapping["details"] = idx
        if "AMOUNT" in norm:
            mapping["amount"] = idx
    return mapping


# ---------------------------------------------------------------------------
# Statement period (year inference)
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(
    r"Statement\s*Period\s*[:\-]?\s*"
    r"(?P<start>[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
    r"\s*(?:-|–|—|to)\s*"
    r"(?P<end>[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)


def _parse_full_date(s: str) -> date | None:
    s = s.strip().replace(",", "")
    for fmt in ("%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_statement_period(text: str) -> tuple[date, date] | None:
    """Return (start, end) dates of the statement period, or None if not found."""
    m = _PERIOD_RE.search(text)
    if not m:
        return None
    start = _parse_full_date(m.group("start"))
    end = _parse_full_date(m.group("end"))
    if start is None or end is None:
        return None
    return start, end


def resolve_year(txn_month: int, period: tuple[date, date]) -> int:
    """Pick the correct year for a row whose date is only "Mmm DD".

    If the statement period lives in one calendar year, trivially return that
    year. Otherwise (Dec->Jan crossover), months >= start-month belong to the
    earlier year and months <= end-month belong to the later year.
    """
    start, end = period
    if start.year == end.year:
        return start.year
    if txn_month >= start.month:
        return start.year
    return end.year


def parse_row_date(raw: str, period: tuple[date, date]) -> date | None:
    """Parse a row's short-form date like "Mar 27" using the statement period."""
    raw = raw.strip().rstrip(".")
    if not raw:
        return None
    for fmt in ("%b %d", "%B %d"):
        try:
            partial = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    year = resolve_year(partial.month, period)
    try:
        return date(year, partial.month, partial.day)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"^[+\-\$]*\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*-?$")


def parse_amount(raw: str) -> Decimal | None:
    """Parse a Scotiabank amount string. Trailing '-' denotes a credit (negative).

    Returns None if `raw` doesn't look like a number at all (lets callers
    distinguish transaction rows from garbage rows).
    """
    s = raw.strip().replace("$", "").replace(" ", "")
    if not s:
        return None
    # Scotiabank convention: trailing dash = credit (payment).
    negative = False
    if s.endswith("-"):
        negative = True
        s = s[:-1]
    # Also handle leading minus, just in case.
    if s.startswith("-"):
        negative = not negative
        s = s[1:]
    s = s.replace(",", "")
    if not s or not re.match(r"^\d+(\.\d+)?$", s):
        return None
    try:
        amount = Decimal(s)
    except InvalidOperation:
        return None
    return -amount if negative else amount


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------

_FX_LINE_RE = re.compile(r"^\s*AMT\s+[\d.,]+\s+[A-Z]{3}\s*$")
# Inline FX suffix: in some Docling renderings the FX subline ends up fused
# into the main DETAILS cell with only single spaces, e.g.
# "ANTHROPIC AMT 10.36 USD". We strip it off the merchant name for payee
# extraction, but keep the full string in Description.
_INLINE_FX_SUFFIX_RE = re.compile(r"\s+AMT\s+[\d.,]+\s+[A-Z]{3}\s*$")
_REF_RE = re.compile(r"^\d{3,}$")
_SUBTOTAL_RE = re.compile(r"^SUB[- ]?TOTAL\b", re.IGNORECASE)
_CARDHOLDER_RE = re.compile(r"XXXX\s*XXXX", re.IGNORECASE)


@dataclass
class _Cell:
    ref: str
    trans_date: str
    post_date: str
    details: str
    amount: str


def _get_cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _cells_for(row: list[str], cols: dict[str, int]) -> _Cell:
    """Build a _Cell from a raw row, merging any unnamed columns between the
    DETAILS column and the AMOUNT column into `details` (space-separated).

    Docling sometimes splits the transaction description across two adjacent
    cells — the named "DETAILS" column plus an unnamed secondary column — so
    anything between DETAILS and AMOUNT belongs in the description."""
    details_idx = cols.get("details")
    amount_idx = cols.get("amount")
    if details_idx is not None and amount_idx is not None and amount_idx > details_idx + 1:
        chunks = [_get_cell(row, i) for i in range(details_idx, amount_idx)]
        details = "  ".join(c for c in chunks if c)
    else:
        details = _get_cell(row, details_idx)
    return _Cell(
        ref=_get_cell(row, cols.get("ref")),
        trans_date=_get_cell(row, cols.get("trans_date")),
        post_date=_get_cell(row, cols.get("post_date")),
        details=details,
        amount=_get_cell(row, amount_idx),
    )


def is_fx_subline_row(cell: _Cell) -> bool:
    """A standalone row carrying only an `AMT xx USD` FX subline."""
    if cell.ref or cell.trans_date or cell.post_date or cell.amount:
        return False
    return bool(_FX_LINE_RE.match(cell.details))


def is_subtotal_row(cell: _Cell) -> bool:
    return bool(_SUBTOTAL_RE.match(cell.details))


def is_cardholder_banner(cell: _Cell) -> bool:
    if cell.trans_date or cell.post_date or cell.amount:
        return False
    return bool(_CARDHOLDER_RE.search(cell.details))


def is_transaction_row(cell: _Cell) -> bool:
    """Heuristic: a 3-digit ref AND a parseable amount AND a trans date."""
    if not _REF_RE.match(cell.ref.strip()):
        return False
    if parse_amount(cell.amount) is None:
        return False
    return bool(cell.trans_date)


# ---------------------------------------------------------------------------
# Details -> (Payee, Description) split + FX subline merging
# ---------------------------------------------------------------------------

_MULTISPACE_RE = re.compile(r"\s{2,}")


def split_payee(details: str) -> tuple[str, str]:
    """Return (payee, description) from a DETAILS cell.

    Payee = first chunk before 2+ whitespace on the first physical line, with
    any inline ` AMT X.XX USD` FX suffix stripped. If there's no multi-space
    separator, the whole (FX-stripped) first line is the payee. Description
    carries the cleaned form of the full DETAILS (including any FX info), with
    newlines preserved between physical lines.
    """
    # IMPORTANT: split on the *original* first line, before collapsing runs of
    # spaces. The 2+ whitespace gap is the very signal we're using to find the
    # payee/location boundary — collapsing it first erases it.
    first_line_raw = details.split("\n", 1)[0].strip()
    parts = _MULTISPACE_RE.split(first_line_raw, maxsplit=1)
    payee_raw = parts[0].strip()
    # Some Docling renderings fuse the FX subline into the first chunk with
    # only single spaces (`ANTHROPIC AMT 10.36 USD`). Strip that off the
    # merchant name so it doesn't leak into Payee — the FX info is still
    # preserved in Description below.
    payee = _INLINE_FX_SUFFIX_RE.sub("", payee_raw).strip()
    description = _collapse_whitespace_per_line(details).strip()
    return payee, description


def _collapse_whitespace_per_line(s: str) -> str:
    # Keep newlines (they separate the main line from any FX subline) but
    # collapse runs of spaces/tabs inside each line.
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in s.splitlines()]
    return "\n".join(line for line in lines if line)


def _append_fx_subline(description: str, fx_line: str) -> str:
    fx_line = fx_line.strip()
    if not fx_line:
        return description
    if fx_line in description:
        return description
    return f"{description}\n{fx_line}" if description else fx_line


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class ScotiabankPassportVisaParser(BaseParser):
    """Parser for Scotiabank Passport Visa / Visa Infinite Business credit cards."""

    name = "scotiabank_passport_visa"

    def is_match(self, parsed: ParsedPDF) -> bool:
        text_upper = (parsed.text or "").upper()
        if "SCOTIABANK" not in text_upper:
            return False
        return any(is_transaction_table(t.headers) for t in parsed.tables)

    def extract_transactions(self, parsed: ParsedPDF) -> list[TransactionRow]:
        period = parse_statement_period(parsed.text or "")
        if period is None:
            raise ValueError(
                "Could not find 'Statement Period Mmm DD, YYYY - Mmm DD, YYYY' "
                "in the PDF text; cannot infer transaction year."
            )
        rows: list[TransactionRow] = []
        for table in parsed.tables:
            if not is_transaction_table(table.headers):
                continue
            rows.extend(self._extract_from_table(table, period))
        return rows

    def _extract_from_table(
        self, table: ExtractedTable, period: tuple[date, date]
    ) -> Iterable[TransactionRow]:
        cols = find_column_indices(table.headers)
        if "details" not in cols or "amount" not in cols or "trans_date" not in cols:
            return []

        out: list[TransactionRow] = []
        for row in table.rows:
            cell = _cells_for(row, cols)

            # Detect and merge an FX subline — either a standalone row, or an
            # embedded newline inside the DETAILS cell of a transaction row
            # (handled inline below).
            if is_fx_subline_row(cell):
                if out:
                    out[-1].Description = _append_fx_subline(
                        out[-1].Description, cell.details
                    )
                continue

            if is_subtotal_row(cell) or is_cardholder_banner(cell):
                continue

            if not is_transaction_row(cell):
                continue

            txn_date = parse_row_date(cell.trans_date, period)
            amount = parse_amount(cell.amount)
            if txn_date is None or amount is None:
                continue

            payee, description = split_payee(cell.details)
            out.append(
                TransactionRow(
                    Date=txn_date,
                    Amount=amount,
                    Payee=payee,
                    Description=description,
                    Reference=cell.ref.strip(),
                    CheckNumber="",
                    source_bank=self.name,
                )
            )
        return out
