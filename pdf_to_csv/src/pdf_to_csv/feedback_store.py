"""SQLite-backed store for user feedback on extracted transactions.

Why: the pipeline is heuristic. Bank-specific parsers cover part of the
real-world space, the generic fallback covers the rest, and both will be
wrong on some rows (OCR alignment, column-classification errors, unknown
sign conventions). The accountant using this pilot is the only person who
can say whether a given row is right. Capturing their corrections gives us:

* a correct CSV for the immediate handoff,
* a dataset we can later mine to build a named parser (e.g.
  `ScotiabankChequingParser`) or to tune the generic heuristics.

Schema is deliberately minimal. One row per correction:

    action        edit | delete | add
    original      JSON of the row as the pipeline produced it (None for `add`)
    corrected     JSON of the row after the user's edit    (None for `delete`)
    user_comment  optional free text

Context columns (source_file, source_bank, statement_title, account_type)
make the log trivially groupable in SQL / pandas without having to dig
into the JSON blobs.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, Field

Action = Literal["edit", "delete", "add"]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class FeedbackRecord(BaseModel):
    """One user correction. `id` is assigned by SQLite; clients omit it."""

    id: Optional[int] = None
    created_at: datetime = Field(default_factory=_now_utc)
    action: Action
    source_file: Optional[str] = None
    source_bank: Optional[str] = None
    statement_title: Optional[str] = None
    account_type: Optional[str] = None
    original: Optional[dict[str, Any]] = None
    corrected: Optional[dict[str, Any]] = None
    user_comment: str = ""


def default_db_path() -> Path:
    """Resolve the feedback DB path.

    Precedence:
        1. PDF_TO_CSV_FEEDBACK_DB env var (explicit path)
        2. ./data/feedback.db  (project-local; works out of the box)
    """
    override = os.environ.get("PDF_TO_CSV_FEEDBACK_DB")
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "data" / "feedback.db"


class FeedbackStore:
    """Thin wrapper over a single-file SQLite DB. Thread-safe per-call via
    `sqlite3.connect`; we don't hold a long-lived connection because the
    pilot's access pattern is infrequent writes and occasional reads."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at      TEXT    NOT NULL,
                    action          TEXT    NOT NULL
                                            CHECK (action IN ('edit','delete','add')),
                    source_file     TEXT,
                    source_bank     TEXT,
                    statement_title TEXT,
                    account_type    TEXT,
                    original_json   TEXT,
                    corrected_json  TEXT,
                    user_comment    TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_created
                    ON feedback(created_at);
                CREATE INDEX IF NOT EXISTS idx_feedback_source
                    ON feedback(source_file, source_bank);
                """
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add(self, record: FeedbackRecord) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO feedback (
                    created_at, action, source_file, source_bank,
                    statement_title, account_type,
                    original_json, corrected_json, user_comment
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.created_at.isoformat(),
                    record.action,
                    record.source_file,
                    record.source_bank,
                    record.statement_title,
                    record.account_type,
                    json.dumps(record.original, separators=(",", ":"))
                        if record.original is not None else None,
                    json.dumps(record.corrected, separators=(",", ":"))
                        if record.corrected is not None else None,
                    record.user_comment,
                ),
            )
            return int(cur.lastrowid or 0)

    def add_many(self, records: list[FeedbackRecord]) -> list[int]:
        return [self.add(r) for r in records]

    def list_all(self, *, limit: Optional[int] = None) -> list[FeedbackRecord]:
        with self._conn() as conn:
            sql = "SELECT * FROM feedback ORDER BY id DESC"
            params: tuple[Any, ...] = ()
            if limit is not None:
                sql += " LIMIT ?"
                params = (int(limit),)
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0])


def _row_to_record(row: sqlite3.Row) -> FeedbackRecord:
    return FeedbackRecord(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        action=row["action"],
        source_file=row["source_file"],
        source_bank=row["source_bank"],
        statement_title=row["statement_title"],
        account_type=row["account_type"],
        original=json.loads(row["original_json"]) if row["original_json"] else None,
        corrected=json.loads(row["corrected_json"]) if row["corrected_json"] else None,
        user_comment=row["user_comment"] or "",
    )
