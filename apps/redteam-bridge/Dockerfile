FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim-bookworm

COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY apps/redteam-bridge/pyproject.toml apps/redteam-bridge/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY apps/redteam-bridge/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

RUN groupadd --system app && \
    useradd --system --gid app --home-dir /nonexistent --no-create-home app && \
    mkdir /data && chown app:app /data && chmod 700 /data

ENV PATH="/app/.venv/bin:${PATH}"
USER app
EXPOSE 8080
CMD ["uvicorn", "redteam_bridge.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
