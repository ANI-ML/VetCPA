"""Tests for the ingress layer: supported-extension validation + HEIC→JPEG
conversion.

The HEIC→JPEG test generates a real HEIF image on the fly using Pillow so we
don't need a fixture file in the repo.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pdf_to_csv.ingest import (
    HeicConversionError,
    SUPPORTED_INPUT_EXTS,
    accepted_types_label,
    is_heic,
    is_image,
    is_pdf,
    is_supported,
    normalize_for_docling,
)


# ---------------------------------------------------------------------------
# Extension helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("statement.pdf", True),
    ("statement.PDF", True),
    ("photo.jpg", True),
    ("photo.jpeg", True),
    ("photo.png", True),
    ("scan.tif", True),
    ("scan.tiff", True),
    ("scan.bmp", True),
    ("phone.heic", True),
    ("phone.HEIF", True),
    ("notes.docx", False),
    ("archive.zip", False),
    ("noext", False),
])
def test_is_supported(name: str, expected: bool) -> None:
    assert is_supported(name) is expected


def test_is_pdf_vs_is_image_is_heic() -> None:
    assert is_pdf("statement.pdf")
    assert not is_pdf("photo.jpg")

    assert is_image("photo.jpg")
    assert is_image("scan.tiff")
    assert is_image("phone.heic")
    assert not is_image("statement.pdf")

    assert is_heic("phone.heic")
    assert is_heic("phone.heif")
    assert not is_heic("photo.jpg")


def test_supported_exts_frozenset_covers_expected_types() -> None:
    # Guard against accidental removal.
    for ext in {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif"}:
        assert ext in SUPPORTED_INPUT_EXTS


def test_accepted_types_label_is_stable() -> None:
    label = accepted_types_label()
    assert "PDF" in label
    assert "HEIC" in label


# ---------------------------------------------------------------------------
# normalize_for_docling: passthrough for non-HEIC
# ---------------------------------------------------------------------------

def test_normalize_passthrough_for_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    assert normalize_for_docling(pdf, work_dir=tmp_path) == pdf


def test_normalize_passthrough_for_small_jpg(tmp_path: Path) -> None:
    # Small, within the size cap — helper should return the original path
    # without opening/re-saving.
    jpg = tmp_path / "photo.jpg"
    Image.new("RGB", (800, 600), color=(100, 150, 200)).save(jpg, format="JPEG")
    assert normalize_for_docling(jpg, work_dir=tmp_path) == jpg


def test_normalize_passthrough_for_small_png(tmp_path: Path) -> None:
    png = tmp_path / "photo.png"
    Image.new("RGB", (800, 600), color=(100, 150, 200)).save(png, format="PNG")
    assert normalize_for_docling(png, work_dir=tmp_path) == png


# ---------------------------------------------------------------------------
# HEIC → JPEG round trip (needs pillow-heif, ships as a hard dep)
# ---------------------------------------------------------------------------

pillow_heif = pytest.importorskip("pillow_heif")


def _write_heic_fixture(path: Path, *, size: tuple[int, int] = (32, 32)) -> None:
    """Generate a minimal valid HEIF file at `path`."""
    pillow_heif.register_heif_opener()
    img = Image.new("RGB", size, color=(200, 100, 50))
    img.save(path, format="HEIF")


def test_normalize_converts_heic_to_jpeg(tmp_path: Path) -> None:
    heic = tmp_path / "phone.heic"
    _write_heic_fixture(heic)

    work = tmp_path / "work"
    work.mkdir()

    jpg = normalize_for_docling(heic, work_dir=work)

    assert jpg != heic
    assert jpg.parent == work
    assert jpg.suffix == ".jpg"
    assert jpg.stem == "phone"
    assert jpg.exists()

    # Round-trip check: Pillow can open the output and it has our dimensions.
    with Image.open(jpg) as out:
        assert out.size == (32, 32)
        assert out.format == "JPEG"


def test_normalize_raises_on_unreadable_heic(tmp_path: Path) -> None:
    bad = tmp_path / "broken.heic"
    bad.write_bytes(b"not really a HEIF file")

    with pytest.raises(HeicConversionError):
        normalize_for_docling(bad, work_dir=tmp_path)


# ---------------------------------------------------------------------------
# Oversized non-HEIC images are downscaled to protect Docling from bomb check
# ---------------------------------------------------------------------------

def test_normalize_resizes_oversized_png(tmp_path: Path) -> None:
    # Build a PNG whose longest edge is well above the 2500px cap. Docling's
    # image pipeline upscales ~9x internally and would otherwise trip PIL's
    # decompression-bomb guard on a 24MP phone photo.
    big = tmp_path / "huge.png"
    Image.new("RGB", (4284, 5712), color=(180, 180, 180)).save(big)

    work = tmp_path / "work"
    work.mkdir()

    out = normalize_for_docling(big, work_dir=work)

    # Should have been resized into work_dir as a JPEG with scaled dimensions.
    assert out != big
    assert out.parent == work
    assert out.suffix == ".jpg"
    with Image.open(out) as converted:
        assert max(converted.size) == 2500
        # Aspect ratio preserved (4284:5712 == ~3:4).
        assert converted.size[0] < converted.size[1]


def test_normalize_passes_small_image_through_unchanged(tmp_path: Path) -> None:
    # Image already within the size cap — skip the round-trip through PIL.
    small = tmp_path / "small.jpg"
    Image.new("RGB", (1000, 800), color=(200, 100, 50)).save(small, format="JPEG")
    work = tmp_path / "work"
    work.mkdir()

    # A PNG-signature hack won't do here since we'd need a real image to
    # peek at. Small JPEG suffices.
    assert normalize_for_docling(small, work_dir=work) == small


def test_normalize_resizes_oversized_heic(tmp_path: Path) -> None:
    # Whole reason this layer exists: a 24+ MP iPhone HEIC should land as a
    # 2500px-long-edge JPEG.
    heic = tmp_path / "iphone.heic"
    _write_heic_fixture(heic, size=(4284, 5712))
    work = tmp_path / "work"
    work.mkdir()

    out = normalize_for_docling(heic, work_dir=work)

    assert out.suffix == ".jpg"
    with Image.open(out) as img:
        assert max(img.size) == 2500
