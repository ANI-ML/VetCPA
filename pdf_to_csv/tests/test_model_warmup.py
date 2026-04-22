"""Tests for the Docling model-warmup background thread."""
from __future__ import annotations

import threading
import time

import pytest

from pdf_to_csv import model_warmup


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Isolate every test: clear shared state and make sure leftover env
    vars from other tests don't bleed in."""
    monkeypatch.delenv("VETCPA_SKIP_WARMUP", raising=False)
    model_warmup._reset_for_tests()
    yield
    model_warmup._reset_for_tests()


def test_initial_state_is_inactive() -> None:
    s = model_warmup.get_warmup_state()
    assert s["started"] is False
    assert s["downloading"] is False
    assert s["ready"] is False
    assert s["error"] is None


def test_skip_env_var_prevents_thread_start(monkeypatch) -> None:
    monkeypatch.setenv("VETCPA_SKIP_WARMUP", "1")
    # If the thread did start, Docling would import and (likely) fail / hang;
    # the env-var gate is the only thing keeping that from happening in CI.
    model_warmup.start_warmup_in_background()
    s = model_warmup.get_warmup_state()
    assert s["started"] is False
    assert s["downloading"] is False


def test_warmup_marks_ready_on_success(monkeypatch) -> None:
    """When _warm_docling returns cleanly, state flips to ready."""
    done = threading.Event()

    def fake_warm() -> None:
        done.set()

    monkeypatch.setattr(model_warmup, "_warm_docling", fake_warm)
    model_warmup.start_warmup_in_background()

    # Give the daemon thread a moment to run.
    assert done.wait(timeout=2.0), "warmup thread never fired _warm_docling"
    # Wait for post-run bookkeeping.
    for _ in range(50):
        s = model_warmup.get_warmup_state()
        if s["ready"]:
            break
        time.sleep(0.02)

    s = model_warmup.get_warmup_state()
    assert s["started"] is True
    assert s["ready"] is True
    assert s["downloading"] is False
    assert s["error"] is None
    assert s["started_at"] is not None
    assert s["finished_at"] is not None


def test_warmup_records_error_without_crashing(monkeypatch) -> None:
    """A failure inside _warm_docling becomes a user-facing error string,
    not a dead server."""
    def fake_warm() -> None:
        raise RuntimeError("simulated HF outage")

    monkeypatch.setattr(model_warmup, "_warm_docling", fake_warm)
    model_warmup.start_warmup_in_background()

    for _ in range(50):
        s = model_warmup.get_warmup_state()
        if s["error"] is not None:
            break
        time.sleep(0.02)

    s = model_warmup.get_warmup_state()
    assert s["ready"] is False
    assert s["downloading"] is False
    assert s["error"] is not None
    assert "simulated HF outage" in s["error"]


def test_start_is_idempotent(monkeypatch) -> None:
    """Calling start twice doesn't spawn two workers or reset state."""
    call_count = {"n": 0}

    def fake_warm() -> None:
        call_count["n"] += 1
        time.sleep(0.05)

    monkeypatch.setattr(model_warmup, "_warm_docling", fake_warm)
    model_warmup.start_warmup_in_background()
    model_warmup.start_warmup_in_background()
    model_warmup.start_warmup_in_background()
    time.sleep(0.2)
    assert call_count["n"] == 1
