"""Unit + end-to-end tests for the Scotiabank Passport Visa parser.

End-to-end test uses a synthetic `ParsedPDF` whose structure mirrors the real
April 2025 statement: two tables (page 1 and page 3 splits), FX sublines,
trailing-minus payments, cardholder banner, subtotal rows, and the statement
period in the markdown text.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from pdf_to_csv.docling_client import ExtractedTable, ParsedPDF
from pdf_to_csv.parsers.scotiabank_passport_visa import (
    ScotiabankPassportVisaParser,
    _cells_for,
    find_column_indices,
    is_cardholder_banner,
    is_fx_subline_row,
    is_subtotal_row,
    is_transaction_row,
    is_transaction_table,
    parse_amount,
    parse_row_date,
    parse_statement_period,
    resolve_year,
    split_payee,
)


# ---------------------------------------------------------------------------
# Header / column detection
# ---------------------------------------------------------------------------

def test_is_transaction_table_matches_scotiabank_headers() -> None:
    assert is_transaction_table(
        ["REF.#", "TRANS. DATE", "POST DATE", "DETAILS", "AMOUNT($)"]
    )


def test_is_transaction_table_tolerates_whitespace_and_case() -> None:
    assert is_transaction_table(
        ["ref #", "trans.\ndate", "post\ndate", "Details", "amount ($)"]
    )


def test_is_transaction_table_rejects_unrelated_tables() -> None:
    assert not is_transaction_table(["Cash Advances", "22.99%"])
    assert not is_transaction_table(["Points earned (1.5x earn rate**)", "2,583"])


def test_find_column_indices_basic() -> None:
    headers = ["REF.#", "TRANS. DATE", "POST DATE", "DETAILS", "AMOUNT($)"]
    idx = find_column_indices(headers)
    assert idx == {"ref": 0, "trans_date": 1, "post_date": 2, "details": 3, "amount": 4}


def test_find_column_indices_is_exclusive_on_trans_vs_post() -> None:
    # If a Docling render fuses the two into one cell, we refuse to alias them.
    headers = ["REF.#", "TRANS. DATE POST DATE", "DETAILS", "AMOUNT($)"]
    idx = find_column_indices(headers)
    assert "trans_date" not in idx
    assert "post_date" not in idx
    assert idx["details"] == 2
    assert idx["amount"] == 3


# ---------------------------------------------------------------------------
# Statement period / year inference
# ---------------------------------------------------------------------------

def test_parse_statement_period_canonical_form() -> None:
    text = "Statement Period Mar 28, 2025 - Apr 27, 2025\nStatement Date Apr 27, 2025"
    period = parse_statement_period(text)
    assert period == (date(2025, 3, 28), date(2025, 4, 27))


def test_parse_statement_period_returns_none_when_absent() -> None:
    assert parse_statement_period("some unrelated text") is None


def test_resolve_year_same_year() -> None:
    period = (date(2025, 3, 28), date(2025, 4, 27))
    assert resolve_year(3, period) == 2025
    assert resolve_year(4, period) == 2025


def test_resolve_year_crossing_december_to_january() -> None:
    period = (date(2024, 12, 28), date(2025, 1, 27))
    assert resolve_year(12, period) == 2024
    assert resolve_year(1, period) == 2025


def test_parse_row_date_uses_period_year() -> None:
    period = (date(2025, 3, 28), date(2025, 4, 27))
    assert parse_row_date("Mar 27", period) == date(2025, 3, 27)
    assert parse_row_date("Apr 1", period) == date(2025, 4, 1)


def test_parse_row_date_rejects_garbage() -> None:
    period = (date(2025, 3, 28), date(2025, 4, 27))
    assert parse_row_date("", period) is None
    assert parse_row_date("not-a-date", period) is None


# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------

def test_parse_amount_positive() -> None:
    assert parse_amount("37.00") == Decimal("37.00")
    assert parse_amount("  14.85  ") == Decimal("14.85")
    assert parse_amount("$147.54") == Decimal("147.54")


def test_parse_amount_trailing_minus_is_credit() -> None:
    assert parse_amount("187.26-") == Decimal("-187.26")
    assert parse_amount("1,500.00-") == Decimal("-1500.00")
    assert parse_amount("3,445.26-") == Decimal("-3445.26")


def test_parse_amount_leading_minus_also_handled() -> None:
    assert parse_amount("-42.00") == Decimal("-42.00")


def test_parse_amount_rejects_non_numeric() -> None:
    assert parse_amount("") is None
    assert parse_amount("AMT 10.36 USD") is None
    assert parse_amount("abc") is None


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------

def _cell(ref="", trans="", post="", details="", amount=""):
    # Convenience: build a cell bundle inline.
    cols = {"ref": 0, "trans_date": 1, "post_date": 2, "details": 3, "amount": 4}
    return _cells_for([ref, trans, post, details, amount], cols)


def test_is_fx_subline_row_detects_usd_amt_only() -> None:
    assert is_fx_subline_row(_cell(details="AMT 10.36 USD"))
    assert is_fx_subline_row(_cell(details="AMT 1,500.00 USD"))
    # Real transaction row — not an FX subline.
    assert not is_fx_subline_row(_cell(ref="001", trans="Mar 27", details="FACEBK", amount="37.00"))
    # Line that only *contains* AMT but also has other content.
    assert not is_fx_subline_row(_cell(details="ANTHROPIC AMT 10.36 USD"))


def test_is_subtotal_row() -> None:
    assert is_subtotal_row(_cell(details="SUB-TOTAL CREDITS - 4538 XXXX XXXX 6019"))
    assert is_subtotal_row(_cell(details="SUB TOTAL DEBITS"))
    assert not is_subtotal_row(_cell(details="OCCHIOLINO RESTAURANT"))


def test_is_cardholder_banner() -> None:
    assert is_cardholder_banner(
        _cell(details="MR CHRISTOPHER PINARD - 4538 XXXX XXXX 6019")
    )
    # With other columns populated, it's not a banner.
    assert not is_cardholder_banner(
        _cell(
            trans="Mar 27",
            details="MR CHRISTOPHER PINARD - 4538 XXXX XXXX 6019",
            amount="1.00",
        )
    )


def test_is_transaction_row_requires_ref_amount_and_date() -> None:
    assert is_transaction_row(
        _cell(ref="001", trans="Mar 27", post="Mar 28", details="FACEBK", amount="37.00")
    )
    assert not is_transaction_row(_cell(ref="001", trans="Mar 27", details="FACEBK"))
    assert not is_transaction_row(_cell(ref="", trans="Mar 27", details="FACEBK", amount="37.00"))
    assert not is_transaction_row(_cell(ref="ABC", trans="Mar 27", details="FACEBK", amount="37.00"))


# ---------------------------------------------------------------------------
# Payee / description split
# ---------------------------------------------------------------------------

def test_split_payee_takes_first_chunk_before_double_space() -> None:
    payee, desc = split_payee("ANTHROPIC    ANTHROPIC.COMCA")
    assert payee == "ANTHROPIC"
    assert desc == "ANTHROPIC ANTHROPIC.COMCA"


def test_split_payee_handles_embedded_fx_subline() -> None:
    payee, desc = split_payee("ANTHROPIC    ANTHROPIC.COMCA\nAMT 10.36 USD")
    assert payee == "ANTHROPIC"
    assert desc == "ANTHROPIC ANTHROPIC.COMCA\nAMT 10.36 USD"


def test_split_payee_with_no_multi_space() -> None:
    payee, desc = split_payee("PAYMENT FROM - *****01*9313")
    assert payee == "PAYMENT FROM - *****01*9313"
    assert desc == "PAYMENT FROM - *****01*9313"


def test_split_payee_preserves_multiword_merchant() -> None:
    payee, _ = split_payee("NEJM GRP MASS MED SOC   8008436356 MA")
    assert payee == "NEJM GRP MASS MED SOC"


def test_split_payee_strips_inline_fx_suffix() -> None:
    # Docling sometimes fuses the FX subline into the main DETAILS line with
    # only single spaces — payee should ignore that suffix.
    payee, desc = split_payee("ANTHROPIC AMT 10.36 USD  ANTHROPIC.COMCA")
    assert payee == "ANTHROPIC"
    assert "AMT 10.36 USD" in desc  # still present in description


def test_split_payee_strips_inline_fx_when_no_second_chunk() -> None:
    payee, _ = split_payee("REPLIT, INC. AMT 50.35 USD")
    assert payee == "REPLIT, INC."


# ---------------------------------------------------------------------------
# Six-column table shape: Docling's real Scotiabank output
# ---------------------------------------------------------------------------

HEADERS_6COL = ["REF.#", "TRANS. DATE", "POST DATE", "DETAILS", "", "AMOUNT($)"]


def test_extract_six_column_shape_merges_unnamed_column_into_details() -> None:
    # This is the exact shape Docling produces for the April 2025 Scotiabank
    # statement: DETAILS is split across columns 3 and 4, AMOUNT is at col 5.
    df = pd.DataFrame(
        [
            ["001", "Mar 27", "Mar 28", "FACEBK *8UP3SM4VB2", "650-5434800 CA", "37.00"],
            ["010", "Apr 5", "Apr 5", "PAYMENT FROM -", "*****01*9313", "187.26-"],
            ["013", "Apr 8", "Apr 9", "LinkedIn Pre P361982786", "Mountain ViewCA", "133.32"],
            ["015", "Apr 10", "Apr 11", "REPLIT, INC. AMT 50.35 USD", "REPLIT.COM CA", "71.06"],
        ],
        columns=HEADERS_6COL,
    )
    table = ExtractedTable(
        page_number=3,
        headers=list(df.columns),
        rows=df.astype(str).values.tolist(),
        dataframe=df,
    )
    parsed = ParsedPDF(
        tables=[table],
        text="Scotiabank Passport\nStatement Period Mar 28, 2025 - Apr 27, 2025",
    )

    txns = ScotiabankPassportVisaParser().extract_transactions(parsed)
    by_ref = {t.Reference: t for t in txns}

    assert len(txns) == 4
    # Description merges col 3 and col 4 (the previously-lost account stub / location).
    assert "650-5434800 CA" in by_ref["001"].Description
    assert "*****01*9313" in by_ref["010"].Description
    assert "Mountain ViewCA" in by_ref["013"].Description
    assert "REPLIT.COM CA" in by_ref["015"].Description
    # Payee uses first chunk of first logical line, with inline FX stripped.
    assert by_ref["001"].Payee == "FACEBK *8UP3SM4VB2"
    assert by_ref["013"].Payee == "LinkedIn Pre P361982786"
    assert by_ref["015"].Payee == "REPLIT, INC."
    # Sign convention still works at the 6-col amount index.
    assert by_ref["010"].Amount == Decimal("-187.26")


# ---------------------------------------------------------------------------
# End-to-end: synthetic ParsedPDF mirroring the real April 2025 statement
# ---------------------------------------------------------------------------

HEADERS = ["REF.#", "TRANS. DATE", "POST DATE", "DETAILS", "AMOUNT($)"]


def _table(rows: list[list[str]], page: int) -> ExtractedTable:
    df = pd.DataFrame(rows, columns=HEADERS)
    return ExtractedTable(
        page_number=page,
        headers=list(df.columns),
        rows=df.astype(str).values.tolist(),
        dataframe=df,
    )


def _build_parsed_pdf() -> ParsedPDF:
    # Page 1 — cardholder banner + rows 001-006, row 003+004+006 with FX sublines
    # embedded as \n in DETAILS (the shape Docling produces for row-internal wraps).
    page1_rows = [
        ["", "", "", "MR CHRISTOPHER PINARD - 4538 XXXX XXXX 6019", ""],
        ["001", "Mar 27", "Mar 28", "FACEBK *8UP3SM4VB2   650-5434800 CA", "37.00"],
        ["002", "Mar 30", "Mar 31", "FACEBK *T8LBCPYUB2   650-5434800 CA", "41.00"],
        ["003", "Mar 31", "Mar 31", "ANTHROPIC    ANTHROPIC.COMCA\nAMT 10.36 USD", "14.85"],
        ["004", "Mar 31", "Apr 1", "NEJM GRP MASS MED SOC   8008436356 MA\nAMT 22.57 USD", "32.36"],
        ["005", "Apr 1", "Apr 1", "CANVA* I04473-7765405   CANVA.COM DE", "39.00"],
        ["006", "Apr 1", "Apr 2", "ANVIL   CAMBRIDGE\nAMT 15.00 USD", "21.60"],
    ]

    # Page 3 — continuation. Mix of embedded FX and one *standalone* FX subline
    # row to exercise both code paths. Also: trailing-minus payments and the
    # subtotal rows at the end.
    page3_rows = [
        ["007", "Apr 3", "Apr 3", "ANTHROPIC    ANTHROPIC.COMCA\nAMT 10.38 USD", "14.92"],
        ["008", "Apr 3", "Apr 4", "TWILIO SENDGRID    WWW.TWILIO.COCA", "32.39"],
        # Standalone FX subline row — should merge into row 008's Description.
        ["", "", "", "AMT 22.54 USD", ""],
        ["010", "Apr 5", "Apr 5", "PAYMENT FROM - *****01*9313", "187.26-"],
        ["031", "Apr 20", "Apr 21", "PAYMENT FROM - *****01*9313", "1,500.00-"],
        ["042", "Apr 27", "Apr 27", "INTEREST CHARGES-PURCHASE", "147.54"],
        ["", "", "", "SUB-TOTAL CREDITS - 4538 XXXX XXXX 6019", "3,445.26-"],
        ["", "", "", "SUB-TOTAL DEBITS - 4538 XXXX XXXX 6019", "1,861.67"],
    ]

    text = (
        "## Scotiabank Passport Visa Infinite Business card\n\n"
        "Statement Period Mar 28, 2025 - Apr 27, 2025\n"
        "Statement Date Apr 27, 2025\n"
        "Account # 4538 XXXX XXXX 6019\n"
    )
    return ParsedPDF(
        tables=[_table(page1_rows, page=1), _table(page3_rows, page=3)],
        text=text,
    )


def test_parser_is_match_true_for_scotiabank() -> None:
    parser = ScotiabankPassportVisaParser()
    assert parser.is_match(_build_parsed_pdf())


def test_parser_is_match_false_without_scotiabank_keyword() -> None:
    parser = ScotiabankPassportVisaParser()
    parsed = _build_parsed_pdf()
    parsed.text = parsed.text.replace("Scotiabank", "Other Bank")
    assert not parser.is_match(parsed)


def test_parser_extracts_all_transactions_across_pages() -> None:
    parser = ScotiabankPassportVisaParser()
    txns = parser.extract_transactions(_build_parsed_pdf())

    # 6 rows on page 1 + 5 real rows on page 3 (the FX subline + 2 subtotals are skipped)
    assert len(txns) == 11
    refs = [t.Reference for t in txns]
    assert refs == ["001", "002", "003", "004", "005", "006", "007", "008", "010", "031", "042"]


def test_parser_flips_sign_on_trailing_minus_payments() -> None:
    parser = ScotiabankPassportVisaParser()
    txns = parser.extract_transactions(_build_parsed_pdf())
    by_ref = {t.Reference: t for t in txns}
    assert by_ref["010"].Amount == Decimal("-187.26")
    assert by_ref["031"].Amount == Decimal("-1500.00")
    assert by_ref["042"].Amount == Decimal("147.54")  # interest charge stays positive


def test_parser_uses_trans_date_with_inferred_year() -> None:
    parser = ScotiabankPassportVisaParser()
    txns = parser.extract_transactions(_build_parsed_pdf())
    by_ref = {t.Reference: t for t in txns}
    assert by_ref["001"].Date == date(2025, 3, 27)
    assert by_ref["005"].Date == date(2025, 4, 1)
    assert by_ref["042"].Date == date(2025, 4, 27)


def test_parser_merges_embedded_and_standalone_fx_sublines() -> None:
    parser = ScotiabankPassportVisaParser()
    txns = parser.extract_transactions(_build_parsed_pdf())
    by_ref = {t.Reference: t for t in txns}

    # Embedded (inside DETAILS cell of its own row)
    assert "AMT 10.36 USD" in by_ref["003"].Description
    assert by_ref["003"].Payee == "ANTHROPIC"

    # Standalone FX row (next row in the table) — merged into row 008
    assert "AMT 22.54 USD" in by_ref["008"].Description
    assert by_ref["008"].Payee == "TWILIO SENDGRID"


def test_parser_raises_without_statement_period() -> None:
    import pytest

    parser = ScotiabankPassportVisaParser()
    parsed = _build_parsed_pdf()
    parsed.text = "## Scotiabank\nno period here"
    with pytest.raises(ValueError, match="Statement Period"):
        parser.extract_transactions(parsed)


def test_parser_registers_source_bank() -> None:
    parser = ScotiabankPassportVisaParser()
    txns = parser.extract_transactions(_build_parsed_pdf())
    assert all(t.source_bank == "scotiabank_passport_visa" for t in txns)
