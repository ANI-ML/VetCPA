"""Tests for the model-status module + /models/status endpoint.

The underlying check is "how many bytes are in the cache dirs"; we point
those dirs at a tmp_path and exercise the three meaningful states:

  * empty cache   -> ready=False, percent=0
  * mid-download  -> ready=False, percent in (0, 99]
  * fully cached  -> ready=True,  percent=100

Plus the bundled-build shortcut (DOCLING_ARTIFACTS_PATH set).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pdf_to_csv import api as api_module
from pdf_to_csv import model_status
from pdf_to_csv.api import app


@pytest.fixture(autouse=True)
def isolate_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Redirect the cache-dir probe at a tmp dir and clear env vars so each
    test starts from an empty state."""
    monkeypatch.setenv("PDF_TO_CSV_FEEDBACK_DB", str(tmp_path / "feedback.db"))
    monkeypatch.setenv("VETCPA_SKIP_WARMUP", "1")
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    fake_cache = tmp_path / "fake_cache"
    fake_cache.mkdir()
    monkeypatch.setattr(model_status, "_default_cache_dirs", lambda: [fake_cache])
    # Reset the in-module rate-limiting cache so tests don't see stale snapshots.
    model_status._cache = model_status._StatusCache()
    yield fake_cache


def _write_bytes(path: Path, n: int) -> None:
    path.write_bytes(b"\0" * n)


def test_empty_cache_reports_not_ready(isolate_cache: Path) -> None:
    s = model_status.compute_status()
    assert s.cache_bytes == 0
    assert s.ready is False
    assert s.percent == 0


def test_mid_download_reports_progress_below_100(isolate_cache: Path) -> None:
    # ~30% of the estimated total.
    chunk = int(model_status.ESTIMATED_TOTAL_BYTES * 0.3)
    _write_bytes(isolate_cache / "partial.bin", chunk)
    s = model_status.compute_status()
    assert s.ready is False
    assert 1 <= s.percent <= 99


def test_fully_cached_reports_ready(isolate_cache: Path) -> None:
    # Above both MIN_READY_BYTES and the READY_RATIO threshold.
    size = max(
        model_status.MIN_READY_BYTES + 1,
        int(model_status.ESTIMATED_TOTAL_BYTES * model_status.READY_RATIO) + 1,
    )
    _write_bytes(isolate_cache / "full.bin", size)
    s = model_status.compute_status()
    assert s.ready is True
    assert s.percent == 100


def test_bundled_artifacts_env_var_shortcuts_to_ready(
    isolate_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Empty cache, but DOCLING_ARTIFACTS_PATH is set -> still ready.
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(isolate_cache))
    # Bypass the _default_cache_dirs monkeypatch so the env-var branch runs.
    monkeypatch.setattr(
        model_status, "_default_cache_dirs",
        lambda: [isolate_cache, Path("/nonexistent")],
    )
    model_status._cache = model_status._StatusCache()
    s = model_status.compute_status()
    assert s.bundled is True
    assert s.ready is True
    assert s.percent == 100


def test_endpoint_exposes_status_shape(isolate_cache: Path, monkeypatch) -> None:
    # Stub out the Docling converter build so the lifespan doesn't try to load
    # real ML deps under test.
    monkeypatch.setattr(api_module, "build_converter", lambda **_: object())
    with TestClient(app) as client:
        r = client.get("/models/status")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) >= {
            "cache_bytes", "estimated_total_bytes", "percent",
            "ready", "bundled", "cache_dirs",
        }
        assert body["ready"] is False
        assert body["percent"] == 0
