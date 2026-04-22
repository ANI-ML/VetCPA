"""Data models for the pdf_to_csv pipeline."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from pdf_to_csv.account_type import AccountType


class TransactionRow(BaseModel):
    """One normalized transaction across any bank source.

    The fields up to `CheckNumber` are the canonical per-row CSV schema.
    `StatementTitle` and `AccountType` are added so the output CSV can be
    grouped/sorted by statement for the accountant. `source_bank` and
    `source_file` are audit-only (opt-in via `--include-source`).
    """

    model_config = ConfigDict(use_enum_values=True)

    # Statement-level metadata — same value for every row from the same PDF.
    StatementTitle: str = ""
    AccountType: AccountType = AccountType.OTHER

    # Per-transaction canonical columns.
    Date: date = Field(..., description="Transaction date (ISO YYYY-MM-DD).")
    Amount: Decimal = Field(..., description="Signed amount; negative = payment/credit.")
    Payee: str = ""
    Description: str = ""
    Reference: str = ""
    CheckNumber: str = ""

    # Audit / provenance (excluded from the canonical schema, surfaced
    # only when the caller asks for source columns).
    source_bank: Optional[str] = Field(default=None, exclude=True)
    source_file: Optional[str] = Field(default=None, exclude=True)
