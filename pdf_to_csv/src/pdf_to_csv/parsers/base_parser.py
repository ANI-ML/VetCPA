"""Abstract base class every bank-specific parser implements.

A parser's job is simple:
    1. Decide whether the given PDF looks like one of its statements  (`is_match`)
    2. Turn it into a list of canonical `TransactionRow` objects       (`extract_transactions`)

Both methods receive a `ParsedPDF`, which bundles Docling's extracted tables
plus the full document text. Text matters because header fields like
"Statement Period ..." don't always appear inside a table.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pdf_to_csv.docling_client import ParsedPDF
from pdf_to_csv.models import TransactionRow


class BaseParser(ABC):
    """Contract every bank parser must implement."""

    name: str = "base"

    @abstractmethod
    def is_match(self, parsed: ParsedPDF) -> bool:
        """Return True if this parser recognises the given PDF."""

    @abstractmethod
    def extract_transactions(self, parsed: ParsedPDF) -> list[TransactionRow]:
        """Normalize the matched PDF into canonical TransactionRow objects."""
