"""FastAPI HTTP server for pdf_to_csv.

Endpoints:
    GET  /                               Drag-and-drop front end.
    GET  /health                         Liveness check.
    POST /extract                        Upload PDFs + metadata, get rows back.

Run it locally:
    uvicorn pdf_to_csv.api:app --reload --host 0.0.0.0 --port 8000
    # then browse http://localhost:8000/docs  for Swagger UI

/extract is multipart/form-data:

    files           one or more PDF statements (repeat the field for each).
    titles          OPTIONAL parallel array of titles (one per file).
                    Missing / empty -> we default to the filename stem.
    account_types   OPTIONAL parallel array: visa|mastercard|amex|chequing|
                    savings|other. Missing / empty -> auto-detected from text.

Response shape is controlled by `?format=`:

    ?format=json   (default) — JSON with summary, per-file results, and rows
    ?format=csv              — text/csv attachment
    ?format=excel            — .xlsx attachment

Other query params: `include_source` (adds `source_bank` + `source_file`
columns), `dedupe` (default true), `ocr` (default false; for scanned PDFs).

Design notes:

* The Docling converter is built once at app startup via FastAPI's lifespan and
  reused across every request. Model load is the expensive step and we don't
  want to pay it per request.
* An OCR-enabled converter is built lazily on first OCR request so apps that
  never use OCR don't pay for an unused second copy of the model weights.
* Docling's `convert()` is synchronous and CPU-bound. For this pilot-scale
  deployment we call it inline; under real load we'd push it onto
  `asyncio.to_thread()` so it doesn't block the event loop.
"""
from __future__ import annotations

import io
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from pdf_to_csv.account_type import AccountType
from pdf_to_csv.docling_client import build_converter
from pdf_to_csv.feedback_store import FeedbackRecord, FeedbackStore, default_db_path
from pdf_to_csv.ingest import (
    IngestError,
    accepted_types_label,
    is_supported,
    normalize_for_docling,
)
from pdf_to_csv.model_status import get_cached_status
from pdf_to_csv.pipeline import PdfJob, extract_transactions_from_many

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Lifespan: build Docling converters lazily-once
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-build the no-OCR converter (the common path). The OCR variant is
    # built on demand so tests / non-OCR deployments don't pay for it.
    app.state.converter = None        # type: ignore[attr-defined]
    app.state.converter_ocr = None    # type: ignore[attr-defined]
    # FeedbackStore lazy-initialises its SQLite file on first write, so
    # building it at startup is safe even when the DB file doesn't exist yet.
    app.state.feedback_store = FeedbackStore(default_db_path())  # type: ignore[attr-defined]
    yield


app = FastAPI(
    title="pdf-to-csv",
    version="0.1.2",
    description=(
        "Convert bank/credit-card statement PDFs into a unified CSV/Excel. "
        "Upload PDFs to /extract; choose response shape with ?format="
        "json (default) | csv | excel."
    ),
    lifespan=lifespan,
)


def _get_converter(ocr: bool):
    """Return the shared Docling converter, building it on first use."""
    if ocr:
        if app.state.converter_ocr is None:
            app.state.converter_ocr = build_converter(do_ocr=True)
        return app.state.converter_ocr
    if app.state.converter is None:
        app.state.converter = build_converter(do_ocr=False)
    return app.state.converter


# ---------------------------------------------------------------------------
# GET /, /health
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the drag-and-drop front end."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/models/status")
def models_status() -> dict[str, Any]:
    """First-launch model-download progress for the UI's splash banner.

    Returns cache-dir byte totals + a coarse "ready" flag. The front end
    polls this during the first extraction to show a progress bar so the
    user doesn't think the app has frozen. When `DOCLING_ARTIFACTS_PATH`
    is set (fully-offline bundles), `ready` is always True.
    """
    return get_cached_status().to_dict()


# ---------------------------------------------------------------------------
# POST /extract
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # 50 MB per file — plenty for a statement


def _resolve_account_type(raw: str | None) -> AccountType | None:
    """Map a form-provided string to AccountType. Empty/missing -> None (auto-detect)."""
    if raw is None:
        return None
    s = raw.strip().lower()
    if not s:
        return None
    try:
        return AccountType(s)
    except ValueError as exc:
        valid = ", ".join(m.value for m in AccountType)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown account_type {raw!r}. Valid values: {valid}.",
        ) from exc


@app.post("/extract")
async def extract(
    files: list[UploadFile] = File(..., description="One or more PDF statements."),
    titles: list[str] | None = Form(
        None,
        description=(
            "Optional parallel array of titles (one per file). Defaults to the "
            "filename stem when missing/empty."
        ),
    ),
    account_types: list[str] | None = Form(
        None,
        description=(
            "Optional parallel array: visa|mastercard|amex|chequing|savings|other. "
            "Missing/empty entries are auto-detected from the document text."
        ),
    ),
    format: Literal["json", "csv", "excel"] = Query(
        "json", description="Response shape: JSON (default), CSV attachment, or .xlsx attachment."
    ),
    include_source: bool = Query(
        False, description="Include source_bank + source_file columns in the output."
    ),
    dedupe: bool = Query(
        True, description="Drop rows with identical (Title, Date, Amount, Description)."
    ),
    ocr: bool = Query(
        False, description="Enable Docling OCR (needed for scanned statements)."
    ),
) -> Response:
    """Extract transactions from one or more uploaded PDFs."""
    if not files:
        raise HTTPException(status_code=400, detail="No files submitted.")

    for f in files:
        if not is_supported(f.filename or ""):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported file type: {f.filename!r}. "
                    f"Accepted: {accepted_types_label()}."
                ),
            )

    # Parallel-array validation — if provided, lengths must match `files`.
    if titles is not None and len(titles) != len(files):
        raise HTTPException(
            status_code=400,
            detail=f"titles has {len(titles)} entries; expected {len(files)} (one per file).",
        )
    if account_types is not None and len(account_types) != len(files):
        raise HTTPException(
            status_code=400,
            detail=(
                f"account_types has {len(account_types)} entries; "
                f"expected {len(files)} (one per file)."
            ),
        )

    # Spool uploads to a temp dir so Docling (which takes a Path) can read them.
    # The TemporaryDirectory cleans itself up when the request ends.
    with tempfile.TemporaryDirectory(prefix="pdf_to_csv_") as td:
        work_dir = Path(td)
        jobs: list[PdfJob] = []
        for i, f in enumerate(files):
            original_name = Path(f.filename or "upload").name
            dest = work_dir / original_name
            content = await f.read()
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"{f.filename} exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                )
            if not content:
                raise HTTPException(status_code=400, detail=f"{f.filename} is empty.")
            dest.write_bytes(content)

            # HEIC → JPEG, oversized images → resized JPEG, PDFs / small
            # images pass through. Either IngestError subclass surfaces a
            # user-safe message.
            try:
                docling_path = normalize_for_docling(dest, work_dir=work_dir)
            except IngestError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            # An empty-string title entry counts as "use the default."
            raw_title = titles[i] if titles is not None else None
            title: str | None = raw_title.strip() if raw_title and raw_title.strip() else None
            raw_at = account_types[i] if account_types is not None else None
            jobs.append(PdfJob(
                path=docling_path,
                title=title,
                account_type=_resolve_account_type(raw_at),
                original_filename=original_name,
            ))

        converter = _get_converter(ocr=ocr)
        df, results = extract_transactions_from_many(
            jobs,
            dedupe=dedupe,
            include_source=include_source,
            converter=converter,
        )

    # --- Build summary ---------------------------------------------------
    total_extracted = sum(len(r.transactions) for r in results)
    by_parser: dict[str, int] = {}
    by_account_type: dict[str, int] = {}
    files_summary: list[dict[str, Any]] = []
    for r in results:
        files_summary.append({
            "filename": r.display_name or r.pdf_path.name,
            "title": r.title,
            "account_type": r.account_type.value if hasattr(r.account_type, "value") else str(r.account_type),
            "parser": r.parser_name,
            "rows": len(r.transactions),
            "error": r.error,
        })
        for t in r.transactions:
            pk = t.source_bank or "unknown"
            by_parser[pk] = by_parser.get(pk, 0) + 1
            ak = t.AccountType if isinstance(t.AccountType, str) else t.AccountType.value
            by_account_type[ak] = by_account_type.get(ak, 0) + 1

    summary = {
        "files_processed": len(results),
        "files_failed": sum(1 for r in results if r.error),
        "rows_extracted": total_extracted,
        "rows_after_dedup": len(df),
        "by_parser": by_parser,
        "by_account_type": by_account_type,
    }

    # --- Emit in requested format ---------------------------------------
    if format == "csv":
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="transactions.csv"',
            },
        )

    if format == "excel":
        bbuf = io.BytesIO()
        df.to_excel(bbuf, index=False, engine="openpyxl")
        return Response(
            content=bbuf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="transactions.xlsx"',
            },
        )

    # Default: JSON with rows + summary
    return JSONResponse(
        {
            "summary": summary,
            "files": files_summary,
            "rows": df.to_dict(orient="records"),
        }
    )


# ---------------------------------------------------------------------------
# POST /export — download current (possibly edited) rows as CSV or Excel
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(..., description="Rows to export, in canonical schema.")
    format: Literal["csv", "excel"] = "csv"
    filename: str = Field("transactions", description="Filename stem without extension.")


@app.post("/export")
async def export_rows(body: ExportRequest) -> Response:
    """Render a user-supplied row list as CSV or Excel.

    The front end uses this after the accountant edits rows in-browser, so
    downloads reflect the corrected state instead of the raw pipeline output.
    """
    if not body.rows:
        raise HTTPException(status_code=400, detail="No rows to export.")

    df = pd.DataFrame(body.rows)
    safe_stem = (body.filename or "transactions").strip() or "transactions"

    if body.format == "csv":
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_stem}.csv"'},
        )

    bbuf = io.BytesIO()
    df.to_excel(bbuf, index=False, engine="openpyxl")
    return Response(
        content=bbuf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_stem}.xlsx"'},
    )


# ---------------------------------------------------------------------------
# /feedback — store + list user corrections
# ---------------------------------------------------------------------------

class FeedbackSubmission(BaseModel):
    records: list[FeedbackRecord]


@app.post("/feedback")
async def submit_feedback(body: FeedbackSubmission) -> dict[str, Any]:
    if not body.records:
        raise HTTPException(status_code=400, detail="No feedback records submitted.")
    ids = app.state.feedback_store.add_many(body.records)
    return {"saved": len(ids), "ids": ids}


@app.get("/feedback")
async def list_feedback(
    limit: int = Query(100, ge=1, le=1000,
                       description="Max records to return (most recent first)."),
) -> list[FeedbackRecord]:
    return app.state.feedback_store.list_all(limit=limit)


@app.get("/feedback/count")
async def feedback_count() -> dict[str, int]:
    return {"count": app.state.feedback_store.count()}
