"""Runtime configuration for pdf_to_csv.

Values can be overridden via environment variables. Keep this small and explicit —
we're not trying to be pydantic-settings-heavy for a pilot.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Output schema column order — used by the pipeline and CSV/Excel writers.
    output_columns: tuple[str, ...] = (
        "Date",
        "Amount",
        "Payee",
        "Description",
        "Reference",
        "CheckNumber",
    )

    # When True, collapse foreign-currency "AMT xx USD" sublines into the Description
    # of the preceding transaction row.
    merge_fx_sublines: bool = True

    # Dedup strategy: drop rows where (Date, Amount, Description) all match.
    dedupe_on_merge: bool = True


def load_settings() -> Settings:
    # Placeholder: environment overrides can be added here in later steps.
    _ = os.environ  # keep import used; wire real overrides when needed
    return Settings()
