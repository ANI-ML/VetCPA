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


def _warm_docling() -> None:
    """Actually trigger Docling's first-call model load + HF download.

    We import + call Docling here (not at module top) so a test harness
    that never calls `start_warmup_in_background()` doesn't pay the
    Docling import cost.
    """
    import tempfile
    from pdf_to_csv.docling_client import build_converter

    converter = build_converter(do_ocr=False)

    # Build a tiny blank PDF to feed Docling. The content doesn't matter —
    # what matters is that convert() runs, which forces the layout model
    # (and its dependencies) to download into ~/.cache/docling/.
    with tempfile.TemporaryDirectory(prefix="vetcpa-warmup-") as td:
        pdf_path = Path(td) / "warmup.pdf"
        _write_blank_pdf(pdf_path)
        converter.convert(pdf_path)


def _write_blank_pdf(out: Path) -> None:
    """Write a 1-page blank A4 PDF via Pillow. ~1 KB; valid + readable by
    every PDF tool we've tested against, including Docling."""
    from PIL import Image

    img = Image.new("RGB", (612, 792), color="white")
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
