"""Tests for `pdf-to-csv feedback {list,count,export}`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pdf_to_csv.cli import app
from pdf_to_csv.feedback_store import FeedbackRecord, FeedbackStore


@pytest.fixture()
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the CLI's `default_db_path` at a test-local SQLite file."""
    db_path = tmp_path / "fb.db"
    monkeypatch.setenv("PDF_TO_CSV_FEEDBACK_DB", str(db_path))
    return db_path


def _seed(db_path: Path, n: int = 3) -> None:
    store = FeedbackStore(db_path)
    for i in range(n):
        store.add(FeedbackRecord(
            action="edit",
            source_file=f"statement_{i}.pdf",
            statement_title=f"March Visa {i}",
            account_type="visa",
            original={"idx": i},
            corrected={"idx": i + 1},
        ))


def test_feedback_list_prints_each_record(isolated_store: Path) -> None:
    _seed(isolated_store, n=3)
    result = CliRunner().invoke(app, ["feedback", "list"])
    assert result.exit_code == 0, result.output
    assert "3 record(s)" in result.output
    assert "March Visa 2" in result.output
    assert "March Visa 0" in result.output


def test_feedback_list_when_empty(isolated_store: Path) -> None:
    FeedbackStore(isolated_store)  # create schema
    result = CliRunner().invoke(app, ["feedback", "list"])
    assert result.exit_code == 0
    assert "No feedback recorded" in result.output


def test_feedback_count(isolated_store: Path) -> None:
    _seed(isolated_store, n=5)
    result = CliRunner().invoke(app, ["feedback", "count"])
    assert result.exit_code == 0
    assert result.output.strip() == "5"


def test_feedback_export_writes_json(isolated_store: Path, tmp_path: Path) -> None:
    _seed(isolated_store, n=2)
    out = tmp_path / "export.json"
    result = CliRunner().invoke(app, ["feedback", "export", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    records = json.loads(out.read_text())
    assert len(records) == 2
    # Pydantic serializes datetime as ISO string.
    assert all("created_at" in r and "action" in r for r in records)


def test_feedback_export_respects_limit(isolated_store: Path, tmp_path: Path) -> None:
    _seed(isolated_store, n=5)
    out = tmp_path / "export.json"
    result = CliRunner().invoke(app, ["feedback", "export", "--out", str(out), "-n", "2"])
    assert result.exit_code == 0
    assert len(json.loads(out.read_text())) == 2
