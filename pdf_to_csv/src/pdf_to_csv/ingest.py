"""File-type validation + preprocessing for the pipeline's ingress layer.

Docling handles PDFs natively and images (JPEG, PNG, TIFF, BMP) via its image
pipeline. HEIC / HEIF — the default photo format on iPhones — isn't on that
list, so we convert those to JPEG before Docling sees them. Everything else
passes through untouched.

Used by the API and the CLI as the first thing they do with an uploaded /
supplied path; the pipeline itself assumes it's already been given a
Docling-readable file.
"""
from __future__ import annotations

from pathlib import Path

# Everything we accept on the way in. The HEIC/HEIF branch is routed through
# the converter below; the rest of these go straight to Docling.
SUPPORTED_INPUT_EXTS: frozenset[str] = frozenset({
    ".pdf",
    ".jpg", ".jpeg", ".png",
    ".tif", ".tiff", ".bmp",
    ".heic", ".heif",
})

_IMAGE_EXTS: frozenset[str] = SUPPORTED_INPUT_EXTS - {".pdf"}
_HEIC_EXTS: frozenset[str] = frozenset({".heic", ".heif"})


def is_supported(path: Path | str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_INPUT_EXTS


def is_pdf(path: Path | str) -> bool:
    return Path(path).suffix.lower() == ".pdf"


def is_image(path: Path | str) -> bool:
    return Path(path).suffix.lower() in _IMAGE_EXTS


def is_heic(path: Path | str) -> bool:
    return Path(path).suffix.lower() in _HEIC_EXTS


def accepted_types_label() -> str:
    """Human-readable label for error messages and the UI drop-zone copy."""
    # Alphabetized after .pdf (which leads because it's the main input).
    return "PDF, JPG, JPEG, PNG, TIF, TIFF, BMP, HEIC, HEIF"


class HeicConversionError(RuntimeError):
    """Raised when we were asked to convert a HEIC file but pillow-heif is
    unavailable or the image is unreadable. The error message is safe to
    surface to end users."""


def normalize_for_docling(path: Path, *, work_dir: Path) -> Path:
    """Return a path Docling can read directly.

    * HEIC / HEIF -> convert to JPEG in `work_dir`, return the new path.
    * PDF / JPG / PNG / TIFF / BMP -> returned unchanged.

    The JPEG lands at `work_dir / (path.stem + '.jpg')` and is written at
    quality 92 — high enough for OCR, low enough to keep payloads reasonable.
    Callers are responsible for `work_dir`'s lifetime (typically a
    `tempfile.TemporaryDirectory` that's already handling the upload spool).
    """
    if not is_heic(path):
        return path

    try:
        import pillow_heif  # type: ignore[import-untyped]
        from PIL import Image
    except ImportError as exc:
        raise HeicConversionError(
            "HEIC / HEIF support requires pillow-heif. "
            "Install it with: pip install 'pdf-to-csv[heic]'  "
            "(or: pip install pillow-heif)."
        ) from exc

    # pillow-heif registers with PIL so Image.open() handles .heic directly.
    # Safe to call multiple times.
    pillow_heif.register_heif_opener()

    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            out = work_dir / (path.stem + ".jpg")
            rgb.save(out, format="JPEG", quality=92)
            return out
    except Exception as exc:  # noqa: BLE001 - error is bubbled to the user
        raise HeicConversionError(
            f"Could not read HEIC file {path.name}: {exc}"
        ) from exc
