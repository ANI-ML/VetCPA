"""Tests for the universal fallback parser (`GenericTableParser`).

We feed it synthetic tables that mimic several bank-like layouts to confirm it
can recognize date/amount/description columns by *cell content* regardless of
header naming — and that it rejects non-transaction tables (summaries, KV
blocks) instead of hallucinating rows from them.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd

from pdf_to_csv.docling_client import ExtractedTable, ParsedPDF
from pdf_to_csv.parsers.generic_table import (
    GenericTableParser,
    _try_parse_amount,
    _try_parse_date,
)


def _table(headers: list[str], rows: list[list[str]], page: int = 1) -> ExtractedTable:
    df = pd.DataFrame(rows, columns=headers)
    return ExtractedTable(
        page_number=page,
        headers=headers,
        rows=df.astype(str).values.tolist(),
        dataframe=df,
    )


# ---------------------------------------------------------------------------
# Cell primitives
# ---------------------------------------------------------------------------

def test_try_parse_date_supports_common_formats() -> None:
    assert _try_parse_date("2025-03-27") is not None
    assert _try_parse_date("03/27/2025") is not None
    assert _try_parse_date("03-27-2025") is not None
    assert _try_parse_date("27 Mar 2025") is not None
    assert _try_parse_date("March 27, 2025") is not None


def test_try_parse_date_short_form_requires_fallback_year() -> None:
    assert _try_parse_date("Mar 27") is None
    assert _try_parse_date("Mar 27", fallback_year=2025).isoformat() == "2025-03-27"


def test_try_parse_amount_handles_all_sign_conventions() -> None:
    assert _try_parse_amount("37.00") == Decimal("37.00")
    assert _try_parse_amount("-37.00") == Decimal("-37.00")
    assert _try_parse_amount("37.00-") == Decimal("-37.00")
    assert _try_parse_amount("(37.00)") == Decimal("-37.00")
    assert _try_parse_amount("$1,500.00") == Decimal("1500.00")
    assert _try_parse_amount("1,500.00-") == Decimal("-1500.00")


def test_try_parse_amount_rejects_non_numeric() -> None:
    assert _try_parse_amount("") is None
    assert _try_parse_amount("not a number") is None
    assert _try_parse_amount("AMT 10.36 USD") is None


# ---------------------------------------------------------------------------
# End-to-end: unknown bank with ISO date + leading-minus amounts
# ---------------------------------------------------------------------------

def test_parses_iso_date_signed_amount_layout() -> None:
    # "FakeBank" uses ISO dates, leading-minus for credits, amount on the right.
    table = _table(
        ["Date", "Memo", "Credit/Debit"],
        [
            ["2025-03-27", "STARBUCKS    TORONTO ON", "-4.25"],
            ["2025-03-28", "UBER EATS   SAN FRANCISCO CA", "-23.50"],
            ["2025-04-01", "PAYMENT RECEIVED", "500.00"],
            ["2025-04-03", "SHELL GAS STATION    TORONTO ON", "-60.00"],
        ],
    )
    parsed = ParsedPDF(tables=[table], text="FakeBank Statement 2025")

    txns = GenericTableParser().extract_transactions(parsed)

    assert len(txns) == 4
    assert txns[0].Date.isoformat() == "2025-03-27"
    assert txns[0].Amount == Decimal("-4.25")
    assert txns[0].Payee == "STARBUCKS"
    # Payment-received row keeps its positive sign (bank's convention, not ours).
    assert txns[2].Amount == Decimal("500.00")


def test_parses_parenthesized_credits_layout() -> None:
    # Another common convention: accounting parentheses for negatives.
    table = _table(
        ["Txn Date", "Description", "Amount"],
        [
            ["03/27/2025", "STARBUCKS", "(4.25)"],
            ["03/28/2025", "PAYROLL DEPOSIT", "2,500.00"],
            ["03/29/2025", "SHELL", "(60.00)"],
        ],
    )
    parsed = ParsedPDF(tables=[table], text="")

    txns = GenericTableParser().extract_transactions(parsed)

    assert len(txns) == 3
    assert txns[0].Amount == Decimal("-4.25")
    assert txns[1].Amount == Decimal("2500.00")
    assert txns[2].Amount == Decimal("-60.00")


def test_parses_short_dates_using_statement_period_year_hint() -> None:
    # Short-form dates need a fallback year pulled from the document text.
    table = _table(
        ["Date", "Detail", "Amount"],
        [
            ["Mar 27", "STARBUCKS", "4.25"],
            ["Apr 1", "SHELL", "60.00"],
        ],
    )
    parsed = ParsedPDF(tables=[table], text="Statement Date Apr 27, 2025")

    txns = GenericTableParser().extract_transactions(parsed)

    assert [t.Date.isoformat() for t in txns] == ["2025-03-27", "2025-04-01"]


def test_skips_non_transaction_tables() -> None:
    # Summary-style KV table — no amount column, should be ignored entirely.
    summary = _table(
        ["Field", "Value"],
        [
            ["New Balance", "$5,894.95"],
            ["Credit limit", "$10,000.00"],
            ["Available credit", "$4,105.05"],
        ],
    )
    # A column that's mostly text, no date/amount consistency.
    rewards = _table(
        ["Points earned (1.5x earn rate**)", "2,583"],
        [["Points earned _ Total", "2,583"]],
    )
    parsed = ParsedPDF(tables=[summary, rewards], text="Something")
    assert GenericTableParser().extract_transactions(parsed) == []


def test_rows_tagged_with_source_bank() -> None:
    table = _table(
        ["Date", "Description", "Amount"],
        [["2025-03-27", "STARBUCKS", "-4.25"], ["2025-03-28", "SHELL", "-60.00"]],
    )
    txns = GenericTableParser().extract_transactions(ParsedPDF(tables=[table], text=""))
    assert all(t.source_bank == "generic_table" for t in txns)


def test_parses_scotia_chequing_online_export_layout() -> None:
    """Regression test for the Scotia 'Account Details' online export —
    weekday-prefixed dates, +$/-$ amount signs, multi-line descriptions,
    and Withdrawals/Deposits/Balance column layout. Was returning 0 rows
    in v0.1.5 because neither the date format nor the `+$` amount
    regex matched."""
    table = _table(
        ["Date", "Description", "Withdrawals", "Deposits", "Balance"],
        [
            ["Wed, Apr. 22, 2026", "Miscellaneous Payment\nPinard Christopher Joseph",
             "", "+$3,578.05", "$14,768.57"],
            ["Tue, Apr. 21, 2026", "Customer Transfer Dr.\nPc To 4538170641216019",
             "-$3,000.00", "", "$11,190.52"],
            ["Wed, Apr. 15, 2026", "Miscellaneous Payment\nAni.Ml Health Inc.",
             "", "+$478.88", "$13,890.52"],
            ["Tue, Apr. 14, 2026", "Service Charge\nInterac E-Transfer Fee",
             "-$1.00", "", "$13,411.64"],
        ],
    )
    parsed = ParsedPDF(tables=[table], text="Business Chequing - ****9313")

    txns = GenericTableParser().extract_transactions(parsed)

    assert len(txns) == 4, f"expected 4 rows, got {len(txns)}: {txns!r}"
    amounts = [t.Amount for t in txns]
    assert amounts[0] == Decimal("3578.05")   # +$ deposit
    assert amounts[1] == Decimal("-3000.00")  # -$ withdrawal
    assert amounts[2] == Decimal("478.88")    # +$ deposit
    assert amounts[3] == Decimal("-1.00")     # -$ withdrawal
    # Dates round-trip through the weekday-prefixed format.
    assert txns[0].Date.isoformat() == "2026-04-22"
    assert txns[3].Date.isoformat() == "2026-04-14"
    # Balance column must not bleed in as amounts.
    for t in txns:
        assert t.Amount not in {Decimal("14768.57"), Decimal("11190.52")}


def test_try_parse_amount_accepts_leading_plus() -> None:
    assert _try_parse_amount("+$3,578.05") == Decimal("3578.05")
    assert _try_parse_amount("+478.88") == Decimal("478.88")
    assert _try_parse_amount("+$0.01") == Decimal("0.01")


def test_try_parse_date_accepts_weekday_prefixed_formats() -> None:
    # Scotia online export format
    d = _try_parse_date("Wed, Apr. 22, 2026")
    assert d is not None and d.isoformat() == "2026-04-22"
    d = _try_parse_date("Monday, March 22 2026")
    assert d is not None and d.isoformat() == "2026-03-22"


def test_parses_chequing_debit_credit_balance_layout() -> None:
    # Chequing/savings layout: three numeric columns — Withdrawals (debit),
    # Deposits (credit), and a running Balance. The generic parser should
    # combine the first two into a signed Amount and ignore Balance.
    table = _table(
        ["Date", "Description", "Withdrawals/Debits ($)", "Deposits/Credits ($)", "Balance ($)"],
        [
            ["02/27/2026", "BALANCE FORWARD", "", "", "157,637.74"],
            ["03/02/2026", "MISC PAYMENT INSTINCT", "", "7,810.08", "149,827.66"],
            ["03/02/2026", "DEBIT MEMO E-TRANSFER", "1,582.00", "", "151,409.66"],
            ["03/03/2026", "INSURANCE", "886.54", "", "152,296.20"],
        ],
    )
    parsed = ParsedPDF(tables=[table], text="")

    txns = GenericTableParser().extract_transactions(parsed)

    # BALANCE FORWARD has no debit/credit -> skipped. 3 real transactions.
    assert len(txns) == 3
    amounts = [t.Amount for t in txns]
    assert amounts[0] == Decimal("7810.08")   # deposit
    assert amounts[1] == Decimal("-1582.00")  # debit
    assert amounts[2] == Decimal("-886.54")   # debit
    # Descriptions carry the actual description, not the balance.
    assert "INSTINCT" in txns[0].Description
    # Balance figures (157,637.74 / 149,827.66 / ...) should NOT appear as
    # Amounts — the old behavior picked the Balance column.
    balance_figures = {
        Decimal("-157637.74"), Decimal("-149827.66"),
        Decimal("-151409.66"), Decimal("-152296.20"),
    }
    assert not balance_figures.intersection(amounts)


def test_classify_amount_column_edge_cases() -> None:
    from pdf_to_csv.parsers.generic_table import _classify_amount_column

    # Combined signed column: both keywords present -> treated as plain amount.
    assert _classify_amount_column("Credit/Debit") is None
    # Case-insensitive header detection.
    assert _classify_amount_column("withdrawals ($)") == "debit"
    assert _classify_amount_column("DEPOSITS / CREDITS") == "credit"
    assert _classify_amount_column("Balance ($)") == "balance"
    # No hint -> None (lets the single-column path pick the best amount col).
    assert _classify_amount_column("Amount") is None
    assert _classify_amount_column("") is None


def test_resolve_amount_handles_reversal_rows() -> None:
    # Rare but real: a row has values in BOTH debit and credit columns
    # (e.g., accounting adjustments, voided-and-re-entered). Net = credit - debit.
    from pdf_to_csv.parsers.generic_table import _Layout, _resolve_amount

    layout = _Layout(
        date_col=0, description_col=1,
        amount_strategy="debit_credit", amount_cols=(2, 3),
        fallback_year=2026,
    )
    # Debit 100 + Credit 40 -> net -60
    row = ["03/02/2026", "adj", "100.00", "40.00", "10000.00"]
    assert _resolve_amount(row, layout) == Decimal("-60.00")


def test_is_match_is_universal_fallback() -> None:
    p = GenericTableParser()
    # Accepts any PDF that had at least one table.
    assert p.is_match(ParsedPDF(tables=[_table(["A", "B"], [["1", "2"]])], text=""))
    # Rejects PDFs with no tables at all (no signal to work from).
    assert not p.is_match(ParsedPDF(tables=[], text=""))
