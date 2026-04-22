"""Best-effort reporting on Docling's model cache state.

Used by the front end to put a progress bar on the first-launch model
download. Docling itself does a lazy download from HuggingFace Hub the
first time its converter is invoked — about 1.5 GB of weights. While
that's happening the UI would otherwise look frozen; this module lets
us poll cache-dir size and surface real progress.

Deliberately approximate:

* `ESTIMATED_TOTAL_BYTES` is a ceiling we calibrate from a real
  populated cache (see README). We don't pre-download the manifest
  to get an exact number because that's a whole other HTTP round-trip
  and this is just a progress indicator.
* "ready" snaps to True once the cache is within `READY_RATIO` of the
  estimated total. A small safety margin means we don't show the user
  99.8% forever when Docling picks a slightly-smaller model set.
* When the `DOCLING_ARTIFACTS_PATH` env var points at a bundled model
  directory (i.e. `VETCPA_BUNDLE_MODELS=1` builds), we treat that as
  "always ready" — the download UI never surfaces for fully-offline
  bundles.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Tunables — calibrated against a real populated Docling cache
# (~/.cache/docling after one successful extract). Actual total ranges
# 1.4-1.8 GB depending on Docling version and which OCR backend it picks.
ESTIMATED_TOTAL_BYTES: int = 1_600_000_000
READY_RATIO: float = 0.92         # >= 92% of estimate counts as ready
MIN_READY_BYTES: int = 600_000_000  # belt-and-suspenders floor; weights shouldn't be smaller
RECHECK_MIN_INTERVAL_S: float = 1.0  # avoid repeated du -sh-style scans on hot-poll


def _default_cache_dirs() -> list[Path]:
    """Where Docling's models might live. Ordered most-specific first so a
    bundled-models build short-circuits the generic ~/.cache lookup."""
    bundled = os.environ.get("DOCLING_ARTIFACTS_PATH")
    out: list[Path] = []
    if bundled:
        out.append(Path(bundled).expanduser())
    home = Path.home()
    out.extend([
        home / ".cache" / "docling",
        # HuggingFace stashes its own cache; Docling's models ride on top.
        home / ".cache" / "huggingface",
    ])
    return out


def _dir_size_bytes(path: Path) -> int:
    """Sum the sizes of all regular files under `path`. Returns 0 if the
    directory doesn't exist yet. Tolerant of mid-download churn (files
    appearing/disappearing during the walk)."""
    total = 0
    if not path.exists():
        return 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except (FileNotFoundError, PermissionError):
                # Skip files that vanished or we can't stat — HF downloads
                # constantly create/rename temp files.
                continue
    except (FileNotFoundError, PermissionError):
        return total
    return total


@dataclass
class ModelStatus:
    cache_bytes: int
    estimated_total_bytes: int
    ready: bool
    cache_dirs: list[str] = field(default_factory=list)
    bundled: bool = False

    @property
    def percent(self) -> int:
        if self.ready:
            return 100
        if self.estimated_total_bytes <= 0:
            return 0
        p = int(100 * self.cache_bytes / self.estimated_total_bytes)
        # Cap at 99 while not ready so a slightly-undersized cache doesn't
        # flash "100%" and then sit there.
        return min(p, 99)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_bytes": self.cache_bytes,
            "estimated_total_bytes": self.estimated_total_bytes,
            "percent": self.percent,
            "ready": self.ready,
            "bundled": self.bundled,
            "cache_dirs": self.cache_dirs,
        }


class _StatusCache:
    """Tiny in-process cache so `/models/status` polled every 1-2s doesn't
    re-walk the cache dir each time."""

    def __init__(self) -> None:
        self._last_at: float = 0.0
        self._last: ModelStatus | None = None

    def get(self) -> ModelStatus:
        now = time.monotonic()
        if self._last is not None and now - self._last_at < RECHECK_MIN_INTERVAL_S:
            return self._last
        s = compute_status()
        self._last_at = now
        self._last = s
        return s


_cache = _StatusCache()


def compute_status() -> ModelStatus:
    dirs = _default_cache_dirs()
    bundled = bool(os.environ.get("DOCLING_ARTIFACTS_PATH"))
    total = 0
    for d in dirs:
        total += _dir_size_bytes(d)

    ready = bundled or (
        total >= MIN_READY_BYTES and total >= int(ESTIMATED_TOTAL_BYTES * READY_RATIO)
    )
    return ModelStatus(
        cache_bytes=total,
        estimated_total_bytes=ESTIMATED_TOTAL_BYTES,
        ready=ready,
        cache_dirs=[str(d) for d in dirs],
        bundled=bundled,
    )


def get_cached_status() -> ModelStatus:
    """Rate-limited wrapper for the polling endpoint — callers hit this,
    not compute_status directly, to avoid thrashing the filesystem."""
    return _cache.get()
