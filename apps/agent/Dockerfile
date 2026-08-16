FROM ghcr.io/astral-sh/uv:0.11.7 AS uv

FROM python:3.13-slim-bookworm

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/apps/agent

COPY apps/agent/pyproject.toml apps/agent/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY apps/agent/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN groupadd --system app && \
    useradd --system --gid app --home-dir /nonexistent --no-create-home app

ENV PATH="/app/apps/agent/.venv/bin:${PATH}"

WORKDIR /app
USER app

EXPOSE 8000

CMD ["uvicorn", "work_assistant.app:app", "--host", "0.0.0.0", "--port", "8000"]
