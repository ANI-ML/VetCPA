"""Smoke test for the `pdf-to-csv inspect` CLI command.

Patches out `parse_pdf_to_tables` so we don't need Docling (or a real PDF) in
the test run.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from pdf_to_csv import cli as cli_module
from pdf_to_csv.cli import app
from pdf_to_csv.docling_client import ExtractedTable


@pytest.fixture()
def fake_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "statement.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    return p


def test_inspect_prints_table_summary(monkeypatch: pytest.MonkeyPatch, fake_pdf: Path) -> None:
    df = pd.DataFrame(
        {
            "REF.#": ["001", "002", "003"],
            "TRANS. DATE": ["Mar 12", "Mar 13", "Mar 14"],
            "DETAILS": ["STARBUCKS", "SHELL", "LOBLAWS"],
            "AMOUNT($)": ["4.25", "60.00", "120.33"],
        }
    )
    fake_table = ExtractedTable(
        page_number=1,
        headers=list(df.columns),
        rows=df.astype(str).values.tolist(),
        dataframe=df,
    )

    monkeypatch.setattr(
        "pdf_to_csv.docling_client.parse_pdf_to_tables",
        lambda pdf_path, **kwargs: [fake_table],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["inspect", str(fake_pdf), "--rows", "2"])

    assert result.exit_code == 0, result.output
    assert "Found 1 table(s)" in result.output
    assert "Table 1 (page 1)" in result.output
    assert "REF.# | TRANS. DATE | DETAILS | AMOUNT($)" in result.output
    assert "STARBUCKS" in result.output
    assert "SHELL" in result.output
    # --rows 2 should elide the third row
    assert "LOBLAWS" not in result.output
    assert "... (1 more rows)" in result.output


def test_inspect_no_tables(monkeypatch: pytest.MonkeyPatch, fake_pdf: Path) -> None:
    monkeypatch.setattr(
        "pdf_to_csv.docling_client.parse_pdf_to_tables", lambda pdf_path, **kwargs: []
    )
    runner = CliRunner()
    result = runner.invoke(app, ["inspect", str(fake_pdf)])
    assert result.exit_code == 0
    assert "No tables found" in result.output


def test_default_callback_prints_hint() -> None:
    # Guards the callback that shows a hint when no subcommand is given.
    assert cli_module.app is app  # sanity
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "inspect" in result.output
