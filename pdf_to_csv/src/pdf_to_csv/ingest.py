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


class IngestError(RuntimeError):
    """Base for all ingest-layer errors. The message is safe to surface to
    end users (API callers and CLI runs)."""


class HeicConversionError(IngestError):
    """HEIC file unreadable or pillow-heif unavailable."""


class ImageConversionError(IngestError):
    """A supported non-HEIC image couldn't be loaded for resizing."""


# Longest-edge cap for images handed to Docling. Two reasons to cap:
#   1. OCR engines don't gain accuracy above ~2000-2500px on the long edge
#      for typical bank/receipt content; more pixels just burn CPU.
#   2. Docling's image pipeline internally upscales ~9x for layout analysis,
#      so a 24 MP phone photo turns into a 220 MP surface and trips PIL's
#      decompression-bomb guard. Capping input here avoids that cliff.
_MAX_IMAGE_LONG_EDGE = 2500


def _resize_if_oversized(img, *, max_long_edge: int = _MAX_IMAGE_LONG_EDGE):
    """Return `img` downscaled so its longest edge == max_long_edge (or the
    original image, untouched, when it's already within the cap)."""
    from PIL import Image

    w, h = img.size
    longest = max(w, h)
    if longest <= max_long_edge:
        return img
    scale = max_long_edge / longest
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return img.resize(new_size, Image.LANCZOS)


def normalize_for_docling(path: Path, *, work_dir: Path) -> Path:
    """Return a path Docling can read directly.

    * PDF -> passed through unchanged.
    * HEIC / HEIF -> convert to JPEG in `work_dir`.
    * Other images (JPG / PNG / TIFF / BMP) -> passed through unchanged if
      their longest edge is within the size cap; otherwise re-saved as a
      downscaled JPEG in `work_dir`.

    Converted/resized files land at `work_dir / (path.stem + '.jpg')` at
    quality 92 — high enough for OCR, low enough to keep payloads reasonable.
    Callers are responsible for `work_dir`'s lifetime (typically a
    `tempfile.TemporaryDirectory` that's already handling the upload spool).
    """
    if is_pdf(path):
        return path

    if is_heic(path):
        return _convert_heic_to_jpeg(path, work_dir=work_dir)

    if is_image(path):
        return _maybe_resize_image(path, work_dir=work_dir)

    # Not our problem — the caller should have validated extensions up front.
    return path


def _convert_heic_to_jpeg(path: Path, *, work_dir: Path) -> Path:
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
            rgb = _resize_if_oversized(img.convert("RGB"))
            out = work_dir / (path.stem + ".jpg")
            rgb.save(out, format="JPEG", quality=92)
            return out
    except Exception as exc:  # noqa: BLE001 - error is bubbled to the user
        raise HeicConversionError(
            f"Could not read HEIC file {path.name}: {exc}"
        ) from exc


def _maybe_resize_image(path: Path, *, work_dir: Path) -> Path:
    """Return the original path if the image is within our size cap; otherwise
    write a downscaled JPEG into `work_dir` and return that."""
    from PIL import Image

    try:
        with Image.open(path) as img:
            w, h = img.size
            if max(w, h) <= _MAX_IMAGE_LONG_EDGE:
                return path
            resized = _resize_if_oversized(img.convert("RGB"))
            out = work_dir / (path.stem + ".jpg")
            resized.save(out, format="JPEG", quality=92)
            return out
    except Exception as exc:  # noqa: BLE001
        raise ImageConversionError(
            f"Could not read image {path.name}: {exc}"
        ) from exc
