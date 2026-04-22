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


def test_normalize_passthrough_for_jpg(tmp_path: Path) -> None:
    jpg = tmp_path / "photo.jpg"
    # Write a minimal valid-ish JPEG. Content doesn't matter; we just need
    # the helper to skip the HEIC branch entirely.
    jpg.write_bytes(b"\xff\xd8\xff\xd9")
    assert normalize_for_docling(jpg, work_dir=tmp_path) == jpg


def test_normalize_passthrough_for_png(tmp_path: Path) -> None:
    png = tmp_path / "photo.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
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
