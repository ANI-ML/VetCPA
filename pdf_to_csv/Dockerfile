# syntax=docker/dockerfile:1.7
#
# VetCPA — bank statement extraction, in a single offline container.
#
# Build:   docker build -t vetcpa:latest .
# Run:     docker run --rm -p 8000:8000 -v vetcpa-data:/app/data vetcpa:latest
# Browse:  http://localhost:8000
#
# Design notes:
#
# * python:3.11-slim keeps the base small; libheif + libgl + friends are
#   added because Docling's image pipeline shells out to them via Pillow /
#   pillow-heif / OpenCV.
# * PyTorch is pinned to the CPU-only wheel index. We don't need CUDA on a
#   single-user desktop/laptop/EC2-medium, and the CPU wheels save ~3 GB.
# * Docling models download on first request (~2 GB). We mount a named
#   volume at /home/vetcpa/.cache so that cache persists across `docker run`
#   invocations and only happens once. For a fully-offline image (build-time
#   model download), see README §"Fully offline image".
# * We run as a non-root user out of habit — the feedback DB, model cache,
#   and upload spool all live in writable paths owned by that user.

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PDF_TO_CSV_FEEDBACK_DB=/app/data/feedback.db

# System libraries Docling / pillow-heif / EasyOCR need at runtime.
# libgl1 + libglib2.0-0 are the usual missing-from-slim image pair for OpenCV.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libheif1 \
      libheif-dev \
      libgl1 \
      libglib2.0-0 \
      libjpeg62-turbo \
      libpng16-16 \
      curl \
      ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Unprivileged user owns everything writable.
RUN useradd --create-home --shell /bin/bash vetcpa
WORKDIR /app

# Install PyTorch CPU-only first — its wheel is big and rarely changes, so
# giving it its own layer maximises cache hits on future rebuilds.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
      'torch>=2.2,<3' 'torchvision>=0.17,<1'

# Copy only pyproject + requirements so the dep-install layer is cacheable.
COPY pyproject.toml requirements.txt ./
RUN pip install -r requirements.txt

# Now the source. Changes to src/ invalidate only this small layer.
COPY src/ ./src/
RUN pip install --no-deps -e .

# Feedback DB lives here; mount a volume to persist across container restarts.
RUN mkdir -p /app/data && chown -R vetcpa:vetcpa /app /home/vetcpa

USER vetcpa

EXPOSE 8000

# Simple liveness check. Docker waits ~30s on first run for Docling models
# to warm up before deciding the container is unhealthy.
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "pdf_to_csv.api:app", "--host", "0.0.0.0", "--port", "8000"]
