"""Tests for the account-type enum + auto-detection heuristics."""
from __future__ import annotations

from pdf_to_csv.account_type import AccountType, detect_account_type


def test_detect_visa_from_scotiabank_statement_text() -> None:
    text = "Scotiabank Passport Visa Infinite Business card\nStatement Period ..."
    assert detect_account_type(text) == AccountType.VISA


def test_detect_amex_takes_precedence_over_visa_keyword() -> None:
    # A hypothetical statement that happens to mention both — AMEX wins because
    # it's the more specific pattern and is checked first.
    text = "American Express Platinum — merchant partner: VISA International"
    assert detect_account_type(text) == AccountType.AMEX


def test_detect_amex_from_abbreviation() -> None:
    assert detect_account_type("AMEX Platinum Statement") == AccountType.AMEX


def test_detect_mastercard() -> None:
    assert detect_account_type("Your Mastercard statement is enclosed") == AccountType.MASTERCARD
    assert detect_account_type("RBC MASTER CARD ...") == AccountType.MASTERCARD


def test_detect_chequing() -> None:
    assert detect_account_type("TD Chequing Account 1234") == AccountType.CHEQUING
    # US English "checking" also maps to chequing.
    assert detect_account_type("Chase Checking Statement") == AccountType.CHEQUING


def test_detect_savings() -> None:
    assert detect_account_type("Tangerine Savings Account") == AccountType.SAVINGS


def test_detect_returns_other_on_no_match() -> None:
    assert detect_account_type("") == AccountType.OTHER
    assert detect_account_type(None) == AccountType.OTHER  # type: ignore[arg-type]
    assert detect_account_type("unknown financial institution") == AccountType.OTHER


def test_account_type_display_labels_cover_all_values() -> None:
    # Guard: if we add a new AccountType enum member, we must also give it a
    # display label for the UI dropdown — otherwise this test trips.
    for member in AccountType:
        assert member.display  # non-empty label
