"""Tests for the Docling wrapper.

These tests avoid actually running Docling (which downloads ML models on first
use) by exercising the pure helpers directly and patching in a fake converter
where we need end-to-end coverage.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from pdf_to_csv import docling_client
from pdf_to_csv.docling_client import (
    ExtractedTable,
    _extracted_from_dataframe,
    _page_number_of,
    parse_pdf_to_tables,
)


def test_extracted_from_dataframe_stringifies_and_fills_na() -> None:
    df = pd.DataFrame(
        {
            "TRANS. DATE": ["Mar 12", "Mar 13"],
            "DETAILS": ["STARBUCKS", None],
            "AMOUNT($)": [4.25, 12.50],
        }
    )

    et = _extracted_from_dataframe(df, page_number=2, raw="sentinel")

    assert isinstance(et, ExtractedTable)
    assert et.page_number == 2
    assert et.headers == ["TRANS. DATE", "DETAILS", "AMOUNT($)"]
    assert et.rows == [["Mar 12", "STARBUCKS", "4.25"], ["Mar 13", "", "12.5"]]
    assert et.shape == (2, 3)
    assert et.raw == "sentinel"


def test_page_number_of_handles_missing_prov() -> None:
    assert _page_number_of(SimpleNamespace()) is None
    assert _page_number_of(SimpleNamespace(prov=None)) is None
    assert _page_number_of(SimpleNamespace(prov=[])) is None


def test_page_number_of_reads_first_prov_entry() -> None:
    table = SimpleNamespace(prov=[SimpleNamespace(page_no=3), SimpleNamespace(page_no=9)])
    assert _page_number_of(table) == 3


def test_page_number_of_returns_none_on_bad_value() -> None:
    table = SimpleNamespace(prov=[SimpleNamespace(page_no="not a number")])
    assert _page_number_of(table) is None


def test_parse_pdf_to_tables_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_pdf_to_tables(tmp_path / "does_not_exist.pdf")


def test_parse_pdf_to_tables_uses_supplied_converter(tmp_path: Path) -> None:
    # Construct a fake Docling converter that returns one table with a known
    # DataFrame — lets us verify the wrapper plumbing without touching Docling.
    pdf = tmp_path / "statement.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")

    df = pd.DataFrame({"TRANS. DATE": ["Mar 12"], "DETAILS": ["STARBUCKS"], "AMOUNT($)": ["4.25"]})

    fake_table = SimpleNamespace(
        prov=[SimpleNamespace(page_no=1)],
        export_to_dataframe=lambda: df,
    )
    fake_doc = SimpleNamespace(tables=[fake_table])
    fake_result = SimpleNamespace(document=fake_doc)

    class FakeConverter:
        def convert(self, path: Path) -> SimpleNamespace:
            assert Path(path) == pdf
            return fake_result

    tables = parse_pdf_to_tables(pdf, converter=FakeConverter())

    assert len(tables) == 1
    t = tables[0]
    assert t.page_number == 1
    assert t.headers == ["TRANS. DATE", "DETAILS", "AMOUNT($)"]
    assert t.rows == [["Mar 12", "STARBUCKS", "4.25"]]


def test_parse_pdf_to_tables_handles_no_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")

    class FakeConverter:
        def convert(self, path: Path) -> SimpleNamespace:
            return SimpleNamespace(document=SimpleNamespace(tables=None))

    # Also verifies the `tables is None` path doesn't explode.
    monkeypatch.setattr(docling_client, "build_converter", lambda **_: FakeConverter())
    assert parse_pdf_to_tables(pdf) == []
