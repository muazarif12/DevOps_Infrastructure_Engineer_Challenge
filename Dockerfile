# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.13
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS base
ENV PYTHONUNBUFFERED=1
# Keep the HF cache inside /app so it survives the copy into the runtime stage below,
# and so it resolves to the same path regardless of which user (root here, `runner`
# below) is reading it. Without this, download-files (run as root) writes to
# /root/.cache/huggingface, the runtime stage only copies /app, and the turn-detector
# plugin — which loads with local_files_only=True — fails at startup with
# `RuntimeError: ... Could not find file "model_q8.onnx"`.
ENV HF_HOME=/app/.cache/huggingface

# --- Build stage: install deps and pre-fetch model files ---
FROM base AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

# Bring in the source, then finish installing the project itself.
# (.dockerignore keeps secrets, the local SQLite file, and the host .venv out of this context.)
COPY . .
RUN uv sync --locked

# Pre-download the VAD / turn-detector model artifacts so startup is fast and the
# container never needs network access to HuggingFace at runtime. Invoked against
# livekit.agents directly (not `python -m app.worker download-files`, which is
# deprecated as of livekit-agents 1.5.10 and only prints a warning + delegates here).
RUN uv run python -m livekit.agents download-files

# --- Runtime stage: no compilers, non-root user ---
FROM base

ARG UID=10001
RUN adduser --disabled-password --gecos "" --home /app --shell /sbin/nologin --uid "${UID}" runner

COPY --from=build --chown=runner:runner /app /app
WORKDIR /app
USER runner

# Worker health/metrics (see AgentServer(...) in app/worker.py) and the FastAPI service.
# Only one of these is actually served per-container — see docker-compose.yml, which
# runs this same image twice with different commands (worker CMD below; api overrides
# it with `uv run uvicorn app.web:app ...`).
EXPOSE 8000 8081 9091

# Connect to LiveKit and wait for inbound calls. Overridden by docker-compose for the
# api service.
CMD ["uv", "run", "python", "-m", "app.worker", "start"]
