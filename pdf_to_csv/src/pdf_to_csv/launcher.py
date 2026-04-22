"""Desktop launcher — the entry point when VetCPA is bundled as a .app / .exe.

Flow:
  1. Find a free localhost port (so "port already in use" never breaks launch).
  2. Start uvicorn on that port in a background thread.
  3. Wait for GET /health to come back 200, then open the default browser
     to http://127.0.0.1:<port>/.
  4. Keep the main thread alive until the user hits Ctrl-C or closes the app.

Kept tiny on purpose: every import added here is another thing PyInstaller has
to find. The real app lives in `pdf_to_csv.api`.
"""
from __future__ import annotations

import http.client
import logging
import multiprocessing
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


# CRITICAL: called before anything else. In a PyInstaller-frozen macOS .app
# (or Windows .exe), Python's `multiprocessing` re-executes this script from
# the top for every worker it spawns. Docling uses multiprocessing pools for
# OCR/layout analysis, so without `freeze_support()` every worker would run
# main() again — opening a new browser tab each time, starting its own
# uvicorn that port-conflicts with the first, and producing the "20 tabs,
# app keeps reloading" behavior we saw in v0.1.3. This call makes the
# secondary-process branch exit immediately and hand off to the mp worker.
multiprocessing.freeze_support()


DEFAULT_HOST = "127.0.0.1"
HEALTH_PATH = "/health"
STARTUP_TIMEOUT_S = 120  # Docling's first model-load can be slow on cold disks.


def find_free_port(host: str = DEFAULT_HOST) -> int:
    """Ask the OS for an available ephemeral port. Races are cosmetically
    possible but not a real concern for a single-user desktop app."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _wait_for_health(host: str, port: int) -> bool:
    """Poll /health until it answers 200 or we run out of time."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection(host, port, timeout=1.0)
            conn.request("GET", HEALTH_PATH)
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            if resp.status == 200:
                return True
            _ = body  # not used; read() drains so the connection can close cleanly
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.3)
    return False


def _configure_frozen_paths() -> None:
    """When frozen by PyInstaller, data files (including Docling models we
    pre-warmed at build time) live under `sys._MEIPASS`. The Docling model
    download dir is resolved via env var; if models are bundled, point the
    model cache at the bundled path so the app is fully offline.
    """
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
        bundled_models = bundle_root / "docling_models"
        if bundled_models.exists():
            # docling reads this env var to locate its model artifacts.
            os.environ.setdefault("DOCLING_ARTIFACTS_PATH", str(bundled_models))


def _start_uvicorn(host: str, port: int) -> threading.Thread:
    """Spin uvicorn up in a daemon thread so the browser-open logic can
    proceed once /health is up. Daemon means the server dies automatically
    when the launcher's main thread exits.

    NOTE: we pass the `app` object directly, not the "module:attr" string
    form. Uvicorn's string form defers the import until startup and uses
    importlib.import_module, which trips up in PyInstaller-frozen bundles
    (pdf_to_csv.api isn't discoverable via the normal import machinery at
    that point). Importing the app up-front and handing the object to
    uvicorn sidesteps that entirely.
    """
    import uvicorn
    from pdf_to_csv.api import app

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        # `workers=1` — single desktop user; no point spawning more. Also
        # required when passing an app instance (uvicorn refuses workers>1
        # unless it can re-import the app by string).
        workers=1,
    )
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    t.start()
    return t


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[VetCPA] %(message)s")
    log = logging.getLogger("launcher")

    _configure_frozen_paths()

    host = DEFAULT_HOST
    port = find_free_port(host)
    url = f"http://{host}:{port}/"

    log.info("Starting VetCPA server on %s ...", url)
    server_thread = _start_uvicorn(host, port)

    if not _wait_for_health(host, port):
        log.error("Server did not become healthy within %ss; quitting.", STARTUP_TIMEOUT_S)
        return 1

    log.info("Opening browser: %s", url)
    try:
        webbrowser.open(url, new=1, autoraise=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not open browser automatically (%s). Visit %s manually.", exc, url)

    log.info("VetCPA is running. Close this window or press Ctrl-C to quit.")
    try:
        # Park the main thread until the user terminates. Using a generous
        # sleep loop (rather than server_thread.join()) means a clean Ctrl-C
        # tears down promptly on every platform.
        while server_thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    return 0


if __name__ == "__main__":
    # Second freeze_support() call — canonical position per Python docs.
    # Belt-and-suspenders with the module-top call.
    multiprocessing.freeze_support()
    raise SystemExit(main())
