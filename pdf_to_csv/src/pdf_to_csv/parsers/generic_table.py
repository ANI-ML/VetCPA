"""Universal fallback parser for unknown bank/credit-card statement layouts.

When no bank-specific parser claims a PDF, this one takes a swing. It looks at
every extracted table and tries to identify, by *cell content* (not header
text), which columns look like dates, amounts, and descriptions. That makes it
independent of any particular bank's labeling conventions — the tradeoff is
lower fidelity than a bespoke parser (no sign-convention knowledge, no FX
subline merging, no payee splitting that understands the bank's formatting).

Produced rows are tagged `source_bank="generic_table"` so the accountant can
see in the output which rows came from the fallback and may need scrutiny.

Promotion path: once a bank shows up a few times, promote it into a named
parser under `parsers/<bank>.py` (using Scotiabank as the reference). The
generic parser exists to keep coverage complete in the meantime, not to be the
long-term home for any specific bank.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pdf_to_csv.docling_client import ExtractedTable, ParsedPDF
from pdf_to_csv.models import TransactionRow
from pdf_to_csv.parsers.base_parser import BaseParser


# ---------------------------------------------------------------------------
# Cell-content classifiers
# ---------------------------------------------------------------------------

# Accept a pretty wide range of common date formats. Each tuple is
# (regex, tuple_of_strptime_formats). Multiple formats per regex let a single
# pattern cover both abbreviated ("Mar") and full ("March") month names
# without duplicating the regex.
_DATE_FORMATS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), ("%Y-%m-%d",)),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), ("%m/%d/%Y",)),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), ("%m-%d-%Y",)),
    (re.compile(r"^\d{2}/\d{2}/\d{2}$"), ("%m/%d/%y",)),
    (re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}$"), ("%d %b %Y", "%d %B %Y")),
    (re.compile(r"^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}$"), ("%b %d %Y", "%B %d %Y")),
    (re.compile(r"^[A-Za-z]{3,9}\s+\d{1,2}$"), ("%b %d", "%B %d")),  # short form, no year
)


def _try_parse_date(raw: str, *, fallback_year: int | None = None) -> date | None:
    """Try every supported format; return the first that works.

    For short formats without a year (e.g. "Mar 27"), `fallback_year` is used.
    The fallback is deliberately simple: callers can pre-compute a sensible
    year from surrounding document text (statement period, filename, etc.).
    """
    s = raw.strip().replace(",", "").rstrip(".")
    if not s:
        return None
    for pattern, fmts in _DATE_FORMATS:
        if not pattern.match(s):
            continue
        for fmt in fmts:
            try:
                parsed = datetime.strptime(s, fmt)
            except ValueError:
                continue
            if "%Y" not in fmt and "%y" not in fmt:
                if fallback_year is None:
                    # Without a year anchor we can't confidently produce an ISO date.
                    return None
                try:
                    return date(fallback_year, parsed.month, parsed.day)
                except ValueError:
                    return None
            return parsed.date()
    return None


# A signed decimal: optional sign, digits (with optional thousand separators),
# optional fractional part, optional trailing minus, optional $ and whitespace.
_AMOUNT_RE = re.compile(
    r"^\s*\$?\s*-?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*-?\s*$"
    r"|^\s*\(\s*\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*\)\s*$"
)


def _try_parse_amount(raw: str) -> Decimal | None:
    """Parse an amount using the widest sane convention set:

    * Leading minus:          `-37.00`
    * Trailing minus:         `37.00-`
    * Accounting parentheses: `(37.00)`
    * Thousand separators:    `1,500.00`
    * Currency symbol:        `$37.00`
    """
    s = raw.strip()
    if not s or not _AMOUNT_RE.match(s):
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.replace("$", "").replace(" ", "")
    if s.endswith("-"):
        negative = not negative
        s = s[:-1]
    if s.startswith("-"):
        negative = not negative
        s = s[1:]
    s = s.replace(",", "")
    if not re.match(r"^\d+(\.\d+)?$", s):
        return None
    try:
        amount = Decimal(s)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def _looks_textual(raw: str) -> bool:
    """Heuristic: cell carries prose/merchant text, not a date or amount."""
    s = raw.strip()
    if not s:
        return False
    if _try_parse_date(s, fallback_year=2000) is not None:
        return False
    if _try_parse_amount(s) is not None:
        return False
    # Reject very short tokens like "CA", "US", short refs that might be numeric.
    return bool(re.search(r"[A-Za-z]", s)) and len(s) >= 3


# ---------------------------------------------------------------------------
# Column scoring
# ---------------------------------------------------------------------------

@dataclass
class _ColumnScore:
    index: int
    date_hits: int
    amount_hits: int
    textual_hits: int
    total_non_empty: int

    @property
    def date_ratio(self) -> float:
        return self.date_hits / self.total_non_empty if self.total_non_empty else 0.0

    @property
    def amount_ratio(self) -> float:
        return self.amount_hits / self.total_non_empty if self.total_non_empty else 0.0

    @property
    def textual_ratio(self) -> float:
        return self.textual_hits / self.total_non_empty if self.total_non_empty else 0.0

    @property
    def mean_length(self) -> float:
        return 0.0  # populated externally if we want to rank description columns


def _score_column(rows: list[list[str]], idx: int) -> _ColumnScore:
    date_hits = 0
    amount_hits = 0
    textual_hits = 0
    non_empty = 0
    for row in rows:
        if idx >= len(row):
            continue
        cell = (row[idx] or "").strip()
        if not cell:
            continue
        non_empty += 1
        if _try_parse_date(cell, fallback_year=2000) is not None:
            date_hits += 1
        elif _try_parse_amount(cell) is not None:
            amount_hits += 1
        elif _looks_textual(cell):
            textual_hits += 1
    return _ColumnScore(
        index=idx,
        date_hits=date_hits,
        amount_hits=amount_hits,
        textual_hits=textual_hits,
        total_non_empty=non_empty,
    )


# A "plausible transactions table" needs at least a date, an amount, and some
# textual column. Everything else is a summary / info table we should skip.
_MIN_ROWS = 2
_COLUMN_MATCH_RATIO = 0.5   # >= 50% of non-empty cells in the column match
_MIN_COLUMN_HITS = 2        # and at least this many actual hits


@dataclass
class _Layout:
    date_col: int
    amount_col: int
    description_col: int
    fallback_year: int | None


def _infer_layout(table: ExtractedTable, fallback_year: int | None) -> _Layout | None:
    if len(table.rows) < _MIN_ROWS:
        return None

    scores = [_score_column(table.rows, i) for i in range(len(table.headers))]

    def _pick(attr: str) -> _ColumnScore | None:
        candidates = [
            s for s in scores
            if getattr(s, attr) >= _MIN_COLUMN_HITS
            and (getattr(s, attr) / s.total_non_empty if s.total_non_empty else 0) >= _COLUMN_MATCH_RATIO
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: getattr(s, attr))

    date_score = _pick("date_hits")
    amount_score = _pick("amount_hits")
    if date_score is None or amount_score is None:
        return None

    # Description: the textual column with the longest mean cell content that
    # isn't already the date or amount column.
    used = {date_score.index, amount_score.index}
    text_candidates = [s for s in scores if s.index not in used and s.textual_hits >= 1]
    if not text_candidates:
        return None
    description_score = max(
        text_candidates,
        key=lambda s: _mean_text_length(table.rows, s.index),
    )

    return _Layout(
        date_col=date_score.index,
        amount_col=amount_score.index,
        description_col=description_score.index,
        fallback_year=fallback_year,
    )


def _mean_text_length(rows: list[list[str]], col_idx: int) -> float:
    lengths = [len((r[col_idx] or "").strip()) for r in rows if col_idx < len(r)]
    return sum(lengths) / len(lengths) if lengths else 0.0


# ---------------------------------------------------------------------------
# Year inference from the document text
# ---------------------------------------------------------------------------

_YEAR_HINT_RES = (
    re.compile(r"Statement\s*Period[^0-9]{0,40}(\d{4})", re.IGNORECASE),
    re.compile(r"Statement\s*Date[^0-9]{0,40}(\d{4})", re.IGNORECASE),
    re.compile(r"(\d{4})"),  # last-ditch: any 4-digit year in the text
)


def _infer_fallback_year(text: str) -> int | None:
    """Pull a year from the document text so short dates ("Mar 27") resolve."""
    if not text:
        return None
    current_year = datetime.now().year
    for regex in _YEAR_HINT_RES:
        for match in regex.finditer(text):
            try:
                candidate = int(match.group(1))
            except (TypeError, ValueError):
                continue
            # Reject clearly-wrong years (e.g. a merchant ID that happens to be 4 digits).
            if 1990 <= candidate <= current_year + 1:
                return candidate
    return None


# ---------------------------------------------------------------------------
# Description -> payee heuristic (intentionally simple)
# ---------------------------------------------------------------------------

_MULTISPACE = re.compile(r"\s{2,}")


def _split_payee(description: str) -> str:
    first = description.split("\n", 1)[0].strip()
    parts = _MULTISPACE.split(first, maxsplit=1)
    return parts[0].strip() if parts else ""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class GenericTableParser(BaseParser):
    """Best-effort parser: matches every PDF, produces rows for every plausible
    table found. Tags rows with `source_bank="generic_table"` for audit."""

    name = "generic_table"

    def is_match(self, parsed: ParsedPDF) -> bool:
        # Always the fallback: accepts anything Docling actually produced tables for.
        return bool(parsed.tables)

    def extract_transactions(self, parsed: ParsedPDF) -> list[TransactionRow]:
        fallback_year = _infer_fallback_year(parsed.text or "")
        out: list[TransactionRow] = []
        for table in parsed.tables:
            layout = _infer_layout(table, fallback_year)
            if layout is None:
                continue
            out.extend(self._rows_from_table(table, layout))
        return out

    def _rows_from_table(self, table: ExtractedTable, layout: _Layout) -> list[TransactionRow]:
        rows: list[TransactionRow] = []
        for row in table.rows:
            if layout.date_col >= len(row) or layout.amount_col >= len(row):
                continue
            raw_date = (row[layout.date_col] or "").strip()
            raw_amount = (row[layout.amount_col] or "").strip()
            raw_desc = (
                (row[layout.description_col] or "").strip()
                if layout.description_col < len(row) else ""
            )

            parsed_date = _try_parse_date(raw_date, fallback_year=layout.fallback_year)
            parsed_amount = _try_parse_amount(raw_amount)
            if parsed_date is None or parsed_amount is None or not raw_desc:
                continue

            # Split the payee on the *raw* description first — the 2+ space
            # gap between merchant and location is the signal, and collapsing
            # whitespace first would erase it.
            payee = _split_payee(raw_desc)
            description = _MULTISPACE.sub(" ", raw_desc)
            rows.append(
                TransactionRow(
                    Date=parsed_date,
                    Amount=parsed_amount,
                    Payee=payee,
                    Description=description,
                    Reference="",
                    CheckNumber="",
                    source_bank=self.name,
                )
            )
        return rows
