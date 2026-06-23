# Single-container, modular-monolith image for the Ericsson E400 (resource-constrained).
# Multi-stage: build deps into a venv, then copy into a slim runtime layer.

FROM python:3.12-slim AS builder

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies first (cached layer), then the project.
COPY pyproject.toml uv.lock* README.md ./
COPY src ./src
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# ---- runtime ----
FROM python:3.12-slim AS runtime

# Non-root runtime user.
RUN useradd --create-home --uid 10001 cil

WORKDIR /app
COPY --from=builder --chown=cil:cil /app /app
COPY --chown=cil:cil config ./config

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CIL_HOST=0.0.0.0 \
    CIL_PORT=8000

USER cil
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["cil"]
