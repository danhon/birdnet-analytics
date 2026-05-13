# syntax=docker/dockerfile:1.7

# NOTE: Best practice is to pin base images by digest (e.g. python:3.12-slim@sha256:...).
# We haven't pinned yet because this repo is currently built locally (no docker client here to
# resolve digests automatically). When we add publishing/CI, we should pin both base images.

# Build/venv stage (uses uv, which is fast and reproducible)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Copy only dependency metadata first (better layer caching)
# README.md is required by hatchling at build time
COPY pyproject.toml uv.lock README.md ./

# Create a project venv and sync deps (frozen => uses uv.lock)
RUN uv venv /app/.venv \
  && uv sync --frozen --no-dev

# Now copy the application code
COPY src ./src
COPY scripts ./scripts
COPY configs ./configs
COPY docs ./docs
COPY README.md ./README.md

# Install the project into the venv (editable not needed in containers)
RUN uv pip install .


# Runtime stage
FROM python:3.12-slim

# Non-root user
RUN useradd -m -u 10001 appuser

WORKDIR /app

# Copy virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy app code (kept separate from venv for clarity; small cost)
COPY --from=builder /app/src /app/src
COPY --from=builder /app/scripts /app/scripts
COPY --from=builder /app/configs /app/configs
COPY --from=builder /app/docs /app/docs
COPY --from=builder /app/README.md /app/README.md
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

ENV PATH="/app/.venv/bin:${PATH}"

# Defaults (override in compose)
ENV BIRDNET_DB_PATH="/data/birdnet.db" \
    BIRDNET_ANALYTICS_TZ="America/Los_Angeles" \
    BIRDNET_ANALYTICS_STATE_DIR="/var/lib/birdnet-analytics"

EXPOSE 8787

# Create state dir + ensure permissions
RUN mkdir -p /var/lib/birdnet-analytics \
  && chown -R appuser:appuser /var/lib/birdnet-analytics

USER appuser

CMD ["uvicorn", "birdnet_analytics.web:app", "--host", "0.0.0.0", "--port", "8787"]
