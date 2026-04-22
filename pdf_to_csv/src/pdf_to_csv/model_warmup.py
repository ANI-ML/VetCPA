"""Proactive Docling model warmup.

Kicks off the one-time ML-model download the moment the server starts, so by
the time the user's browser has finished connecting and rendered the UI the
download is already underway — and so the UI can block the Extract button
until the download finishes.

Why we need this:
    Docling's DocumentConverter doesn't download anything at construction
    time; the 1+ GB HuggingFace pull happens during the first call to
    `.convert()`. If we wait for the user to click Extract before triggering
    that, they stare at a frozen-looking app for several minutes. Users
    reported this; this module exists to fix it.

How it works:
    * On app startup, `start_warmup_in_background()` spawns a daemon thread.
    * That thread builds a DocumentConverter and runs `.convert()` on a
      tiny blank PDF we generate in-memory via Pillow. That's enough to
      force the layout-model download.
    * Thread-safe shared state tracks {started, downloading, ready, error}.
    * `/models/status` exposes that state to the front end so it can gate
      the UI (show a banner, disable Extract) until ready is True.

Set `VETCPA_SKIP_WARMUP=1` to disable — tests set this so lifespan doesn't
spawn a real Docling load during `TestClient` startup.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional


log = logging.getLogger(__name__)


_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "started": False,         # Has a warmup thread been launched?
    "downloading": False,     # Is the thread currently working?
    "ready": False,           # Did it finish successfully?
    "error": None,            # Error message if something crashed.
    "started_at": None,       # epoch seconds
    "finished_at": None,      # epoch seconds
}
_worker: Optional[threading.Thread] = None


def get_warmup_state() -> dict[str, Any]:
    """Snapshot of the current warmup state — safe to call from any thread."""
    with _state_lock:
        return dict(_state)


def start_warmup_in_background() -> None:
    """Launch the warmup thread once per process.

    Idempotent: calling twice is a no-op. Respects `VETCPA_SKIP_WARMUP=1`
    so tests / CI don't accidentally start a Docling download.
    """
    global _worker

    if os.environ.get("VETCPA_SKIP_WARMUP") == "1":
        log.info("Warmup skipped (VETCPA_SKIP_WARMUP=1).")
        return

    with _state_lock:
        if _state["started"]:
            return
        _state["started"] = True
        _state["downloading"] = True
        _state["started_at"] = time.time()

    _worker = threading.Thread(
        target=_run_warmup, name="docling-warmup", daemon=True
    )
    _worker.start()


def _run_warmup() -> None:
    try:
        log.info("Warmup: loading Docling + downloading any missing models...")
        _warm_docling()
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as an error string
        log.exception("Warmup failed.")
        with _state_lock:
            _state["error"] = f"{type(exc).__name__}: {exc}"
            _state["downloading"] = False
        return

    with _state_lock:
        _state["ready"] = True
        _state["downloading"] = False
        _state["finished_at"] = time.time()
        elapsed = _state["finished_at"] - (_state["started_at"] or _state["finished_at"])
    log.info("Warmup: ready in %.1fs", elapsed)


# Docling's model weights live on HuggingFace Hub. We pull each repo
# explicitly in the warmup so both the layout model AND tableformer are
# cached before the user runs a real extract — relying on a dummy convert()
# to happen to trigger every model is fragile (a blank PDF never triggers
# tableformer, so users saw "ready" and then a silent download on first
# extraction).
_DOCLING_MODEL_REPOS: tuple[str, ...] = (
    "docling-project/docling-layout-heron",   # layout + reading-order
    "docling-project/docling-models",          # tableformer + friends
)


def _warm_docling() -> None:
    """Pull every Docling model upfront, then run a sanity-check convert().

    Two stages on purpose:

      1. `huggingface_hub.snapshot_download()` for each known Docling repo.
         This is what actually fills `~/.cache/huggingface/hub/` and is the
         real meat of the ~1.5 GB download the user sees in the progress
         modal.
      2. A real `DocumentConverter.convert()` call on a small table-bearing
         PDF. If (1) succeeded this is near-instant; if (1) raised (e.g. an
         outage on HF's CDN) Docling's lazy-load path still has a chance to
         recover, and the error surfaces back to `/models/status`.
    """
    _prefetch_model_repos()
    _sanity_check_convert()


def _prefetch_model_repos() -> None:
    """Pull each Docling HF repo into the local cache."""
    from huggingface_hub import snapshot_download

    for repo_id in _DOCLING_MODEL_REPOS:
        log.info("Warmup: snapshot_download(%s)", repo_id)
        snapshot_download(repo_id=repo_id)


def _sanity_check_convert() -> None:
    """Run one convert() on a tiny table-bearing PDF — confirms Docling can
    load every model it needs for real statement extraction, not just the
    layout model."""
    import tempfile
    from pdf_to_csv.docling_client import build_converter

    converter = build_converter(do_ocr=False)
    with tempfile.TemporaryDirectory(prefix="vetcpa-warmup-") as td:
        pdf_path = Path(td) / "warmup.pdf"
        _write_warmup_pdf(pdf_path)
        converter.convert(pdf_path)


def _write_warmup_pdf(out: Path) -> None:
    """Render a tiny PDF that contains a *table* so Docling exercises the
    tableformer code path during the warmup. Pillow is already a runtime
    dep (HEIC support) so we don't take on anything new here."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (612, 792), color="white")
    d = ImageDraw.Draw(img)

    # Grid of lines suggesting a 3-column × 4-row table around the top
    # third of the page. Layout-analysis + table-structure models both
    # have something to chew on.
    col_xs = (100, 260, 420, 500)
    row_ys = (120, 170, 220, 270, 320)
    for x in col_xs:
        d.line([(x, row_ys[0]), (x, row_ys[-1])], fill="black", width=1)
    for y in row_ys:
        d.line([(col_xs[0], y), (col_xs[-1], y)], fill="black", width=1)

    # Header + one data row. Content doesn't matter; we just want
    # recognisable text cells.
    d.text((110, 130), "Date",        fill="black")
    d.text((270, 130), "Description", fill="black")
    d.text((430, 130), "Amount",      fill="black")
    d.text((110, 180), "2025-01-01",  fill="black")
    d.text((270, 180), "Warmup row",  fill="black")
    d.text((430, 180), "12.34",       fill="black")

    img.save(str(out), "PDF")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _reset_for_tests() -> None:
    """Clear shared state. Tests call this via fixture to prevent bleed-over."""
    global _worker
    with _state_lock:
        _state.update({
            "started": False,
            "downloading": False,
            "ready": False,
            "error": None,
            "started_at": None,
            "finished_at": None,
        })
    _worker = None
