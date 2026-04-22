"""Account-type classification for bank statements.

We expose a closed enum of the account types an accountant cares about
(Visa / Mastercard / Amex / Chequing / Savings / Other) and a simple
text-based auto-detector. The detector is a best-effort nudge — the user
always has the final say via the UI dropdown.

Detection strategy: search the Docling-extracted markdown text for the
most specific signals first (Amex is checked before Visa because
"American Express Visa" does not exist in practice, but we want a
deterministic order). If nothing matches, we return OTHER, which means
"the user should pick from the dropdown."
"""
from __future__ import annotations

import re
from enum import Enum


class AccountType(str, Enum):
    VISA = "visa"
    MASTERCARD = "mastercard"
    AMEX = "amex"
    CHEQUING = "chequing"
    SAVINGS = "savings"
    OTHER = "other"

    @property
    def display(self) -> str:
        # Labels the UI shows. Keep in sync with the dropdown in index.html.
        return {
            "visa": "Visa",
            "mastercard": "Mastercard",
            "amex": "American Express",
            "chequing": "Chequing / Debit",
            "savings": "Savings",
            "other": "Other",
        }[self.value]


# Most specific first. Word boundaries matter — e.g. `\bVISA\b` guards
# against matching "VISACARD" or random strings containing "visa" as a
# substring, and keeps CHEQUING/CHECKING from being swallowed by a generic
# Visa check elsewhere in the document.
_ACCOUNT_TYPE_PATTERNS: tuple[tuple[re.Pattern[str], AccountType], ...] = (
    (re.compile(r"\bAMERICAN\s+EXPRESS\b|\bAMEX\b", re.IGNORECASE), AccountType.AMEX),
    (re.compile(r"\bMASTER\s?CARD\b", re.IGNORECASE), AccountType.MASTERCARD),
    (re.compile(r"\bVISA\b", re.IGNORECASE), AccountType.VISA),
    (re.compile(r"\bCHEQUING\b|\bCHECKING\b", re.IGNORECASE), AccountType.CHEQUING),
    (re.compile(r"\bSAVINGS\b", re.IGNORECASE), AccountType.SAVINGS),
)


def detect_account_type(text: str) -> AccountType:
    """Best-effort account-type guess from a PDF's extracted text.

    Returns AccountType.OTHER when nothing matches — the UI then prompts
    the user to choose from the dropdown.
    """
    if not text:
        return AccountType.OTHER
    for pattern, account_type in _ACCOUNT_TYPE_PATTERNS:
        if pattern.search(text):
            return account_type
    return AccountType.OTHER
