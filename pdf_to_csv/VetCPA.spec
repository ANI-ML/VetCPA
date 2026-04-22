# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the VetCPA desktop bundle.

Build with (macOS):

    scripts/build_macos.sh

or manually:

    .venv/bin/pyinstaller VetCPA.spec --clean --noconfirm

Output lands in dist/VetCPA.app (macOS) or dist/VetCPA/ (Linux/Windows).

Notes:

* We use PyInstaller's `collect_all()` helper for the heavy ML packages
  (docling, transformers, easyocr, torch) because each has dozens of
  dynamically-imported submodules + non-Python data files that a naive
  include would miss.
* One-dir mode, not one-file: one-file unpacks a ~1.5 GB temp dir on every
  launch and adds 5-10 s of cold-start time. Desktop apps want snappier.
* Docling model weights are NOT bundled by default — they download from
  HuggingFace on first launch (~2 GB, cached at ~/.cache/docling). To pre-
  bundle for a fully-offline bundle, set VETCPA_BUNDLE_MODELS=1 before
  running pyinstaller and make sure the models are already in the build
  machine's cache (e.g. by running the app once with internet first).
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)


# ---------------------------------------------------------------------------
# Heavy-package collection
# ---------------------------------------------------------------------------

datas = []
binaries = []
hiddenimports = []

for pkg in (
    "docling",
    "docling_core",
    "docling_parse",
    "docling_ibm_models",
    "transformers",
    "easyocr",
    "tokenizers",
    "huggingface_hub",
):
    try:
        d, b, hi = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += hi
    except Exception:
        # Missing optional backend (e.g. a plugin docling can use but isn't
        # installed). PyInstaller raises LookupError here; it's fine to skip.
        pass

# These are imported dynamically by fastapi/pydantic/uvicorn in ways
# PyInstaller sometimes misses.
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "pydantic",
    "pydantic_core",
    "pydantic.deprecated.decorator",
    "pillow_heif",
    "openpyxl",
]

# Our own package and its submodules — force them into the bundle so imports
# resolve from anywhere (e.g. uvicorn's string-form app loader).
hiddenimports += collect_submodules("pdf_to_csv")

# Our own static assets.
datas.append(("src/pdf_to_csv/static/index.html", "pdf_to_csv/static"))

# Optional: pre-bundle the Docling model cache. Run the app once on the build
# machine (with internet) to populate ~/.cache/docling, then set this env var.
if os.environ.get("VETCPA_BUNDLE_MODELS") == "1":
    cache_dir = Path.home() / ".cache" / "docling"
    if cache_dir.exists():
        # Mount the cache under docling_models/ in the bundle; launcher.py
        # points DOCLING_ARTIFACTS_PATH at that directory at startup.
        for path in cache_dir.rglob("*"):
            if path.is_file():
                rel = path.relative_to(cache_dir)
                datas.append((str(path), str(Path("docling_models") / rel.parent)))


# ---------------------------------------------------------------------------
# Keep the bundle honest: drop packages we don't actually ship with the app.
# ---------------------------------------------------------------------------

excludes = [
    "tkinter",
    "matplotlib",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "ruff",
    "mypy",
    "sphinx",
]


# ---------------------------------------------------------------------------
# Analysis / PYZ / EXE / BUNDLE
# ---------------------------------------------------------------------------

block_cipher = None

a = Analysis(
    ["src/pdf_to_csv/launcher.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VetCPA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX doesn't play well with large ML binaries
    console=True,       # Keep the log window so the accountant sees
                        # "Opening browser: http://..." — if they close it,
                        # the server shuts down.
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VetCPA",
)

# macOS .app wrapper. No-op on Linux/Windows.
app = BUNDLE(
    coll,
    name="VetCPA.app",
    icon=None,                       # TODO: replace with assets/VetCPA.icns
    bundle_identifier="com.animl.vetcpa",
    info_plist={
        "CFBundleName": "VetCPA",
        "CFBundleDisplayName": "VetCPA",
        "CFBundleShortVersionString": "0.1.5",
        "CFBundleVersion": "0.1.5",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSHumanReadableCopyright": "© 2025 ANI.ML Health",
    },
)
