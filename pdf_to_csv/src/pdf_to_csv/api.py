"""FastAPI HTTP server for pdf_to_csv.

Endpoints:
    GET  /health                         Liveness check.
    POST /extract                        Upload PDFs, get transactions back.

Run it locally:
    uvicorn pdf_to_csv.api:app --reload --host 0.0.0.0 --port 8000
    # then browse http://localhost:8000/docs  for Swagger UI

/extract accepts one or more PDFs via multipart/form-data (`files` field, repeat
for each file). Response shape is controlled by `?format=`:

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

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from pdf_to_csv.docling_client import build_converter
from pdf_to_csv.pipeline import extract_transactions_from_many

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
    yield


app = FastAPI(
    title="pdf-to-csv",
    version="0.1.0",
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
# GET /health
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the drag-and-drop front end."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /extract
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # 50 MB per file — plenty for a statement


@app.post("/extract")
async def extract(
    files: list[UploadFile] = File(..., description="One or more PDF statements."),
    format: Literal["json", "csv", "excel"] = Query(
        "json", description="Response shape: JSON (default), CSV attachment, or .xlsx attachment."
    ),
    include_source: bool = Query(
        False, description="Include source_bank + source_file columns in the output."
    ),
    dedupe: bool = Query(
        True, description="Drop rows with identical (Date, Amount, Description) across files."
    ),
    ocr: bool = Query(
        False, description="Enable Docling OCR (needed for scanned statements)."
    ),
) -> Response:
    """Extract transactions from one or more uploaded PDFs."""
    if not files:
        raise HTTPException(status_code=400, detail="No files submitted.")

    for f in files:
        name = (f.filename or "").lower()
        if not name.endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {f.filename!r}. Only .pdf is accepted.",
            )

    # Spool uploads to a temp dir so Docling (which takes a Path) can read them.
    # The TemporaryDirectory cleans itself up when the request ends.
    with tempfile.TemporaryDirectory(prefix="pdf_to_csv_") as td:
        paths: list[Path] = []
        for f in files:
            dest = Path(td) / Path(f.filename or "upload.pdf").name
            content = await f.read()
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"{f.filename} exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                )
            if not content:
                raise HTTPException(status_code=400, detail=f"{f.filename} is empty.")
            dest.write_bytes(content)
            paths.append(dest)

        converter = _get_converter(ocr=ocr)
        df, results = extract_transactions_from_many(
            paths,
            dedupe=dedupe,
            include_source=include_source,
            converter=converter,
        )

    # --- Build summary ---------------------------------------------------
    total_extracted = sum(len(r.transactions) for r in results)
    by_parser: dict[str, int] = {}
    files_summary: list[dict[str, Any]] = []
    for r in results:
        files_summary.append({
            "filename": r.pdf_path.name,
            "parser": r.parser_name,
            "rows": len(r.transactions),
            "error": r.error,
        })
        for t in r.transactions:
            key = t.source_bank or "unknown"
            by_parser[key] = by_parser.get(key, 0) + 1

    summary = {
        "files_processed": len(results),
        "files_failed": sum(1 for r in results if r.error),
        "rows_extracted": total_extracted,
        "rows_after_dedup": len(df),
        "by_parser": by_parser,
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
