"""Data models for the pdf_to_csv pipeline.

Kept deliberately small for the pilot — we only need one canonical transaction row.
Fleshed out in Step 3.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class TransactionRow(BaseModel):
    """One normalized transaction across any bank source."""

    Date: date = Field(..., description="Transaction date (ISO YYYY-MM-DD).")
    Amount: Decimal = Field(..., description="Signed amount; negative = payment/credit.")
    Payee: str = ""
    Description: str = ""
    Reference: str = ""
    CheckNumber: str = ""

    # Bookkeeping fields (not part of the exported CSV schema).
    source_bank: Optional[str] = Field(default=None, exclude=True)
    source_file: Optional[str] = Field(default=None, exclude=True)
