"""FeedbackStore round-trip tests."""
from __future__ import annotations

from pathlib import Path

from pdf_to_csv.feedback_store import FeedbackRecord, FeedbackStore


def _make_store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback.db")


def test_store_creates_db_and_schema(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.db_path.exists()
    assert store.count() == 0


def test_add_and_list_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    edit = FeedbackRecord(
        action="edit",
        source_file="statement.pdf",
        source_bank="generic_table",
        statement_title="March Chequing",
        account_type="chequing",
        original={"Date": "2025-03-02", "Amount": "886.88", "Payee": "INSURANCE"},
        corrected={"Date": "2025-03-02", "Amount": "-886.88", "Payee": "INSURANCE"},
        user_comment="generic parser put the sign the wrong way",
    )
    add = FeedbackRecord(
        action="add",
        source_file="statement.pdf",
        corrected={"Date": "2025-03-05", "Amount": "-42.00", "Payee": "MISSED FEE"},
    )
    delete = FeedbackRecord(
        action="delete",
        source_file="statement.pdf",
        original={"Date": "2025-03-02", "Amount": "157637.74", "Payee": "BALANCE FORWARD"},
    )

    ids = store.add_many([edit, add, delete])
    assert len(ids) == 3
    assert all(isinstance(i, int) and i > 0 for i in ids)
    assert store.count() == 3

    records = store.list_all()
    # Newest first.
    assert [r.action for r in records] == ["delete", "add", "edit"]

    got_edit = records[2]
    assert got_edit.original["Amount"] == "886.88"
    assert got_edit.corrected["Amount"] == "-886.88"
    assert got_edit.statement_title == "March Chequing"
    assert got_edit.account_type == "chequing"
    assert got_edit.user_comment.startswith("generic parser")


def test_list_respects_limit(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    for i in range(5):
        store.add(FeedbackRecord(action="edit", original={"i": i}, corrected={"i": i + 1}))
    assert len(store.list_all()) == 5
    assert len(store.list_all(limit=2)) == 2
    # Ensure it's the two newest.
    assert store.list_all(limit=1)[0].corrected == {"i": 5}


def test_default_db_path_honors_env_override(monkeypatch, tmp_path: Path) -> None:
    from pdf_to_csv.feedback_store import default_db_path

    override = tmp_path / "custom" / "feedback.db"
    monkeypatch.setenv("PDF_TO_CSV_FEEDBACK_DB", str(override))
    assert default_db_path() == override


def test_default_db_path_falls_back_to_cwd_data_dir(monkeypatch, tmp_path: Path) -> None:
    from pdf_to_csv.feedback_store import default_db_path

    monkeypatch.delenv("PDF_TO_CSV_FEEDBACK_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    assert default_db_path() == tmp_path / "data" / "feedback.db"
