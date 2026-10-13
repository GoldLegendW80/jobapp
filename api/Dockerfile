# api image: FastAPI on python:3.12-slim, dependencies installed by uv from uv.lock.
# Multi-stage: uv and the build cache stay in the builders; the runtimes hold a venv and the code.
# Targets:
# - `runtime` (default, last stage): the api. No dev tools, no Claude Agent SDK.
# - `worker`: the queue worker (python -m app.worker, image jobapp-worker): the same code plus the
#   `agents` dependency group, the Claude Agent SDK, whose wheel bundles the native Claude Code
#   CLI it drives (SDK facts in tasks/agents/todo.md: no Node runtime and no npm package needed),
#   and the `ingestion` group, the libraries the `parser` service reads documents with (that
#   service runs this image under another uid; nothing of the group is imported by the worker).
# - `dev`: every group (dev tools, the SDK, the parser libraries) for compose.dev.yml, which bind-mounts the code and
#   runs uvicorn --reload.

ARG PYTHON_IMAGE=python:3.12.15-slim-trixie

FROM ghcr.io/astral-sh/uv:0.9.28 AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
# The project's default groups are dev, agents and ingestion (pyproject.toml); the api needs none.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --no-install-project

FROM builder AS builder-worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --group agents --group ingestion --no-install-project

FROM builder AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app \
    && chown app:app /app
# CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK: see the worker stage (the dev worker runs on this image).
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
USER app:app
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "app"]

FROM ${PYTHON_IMAGE} AS worker
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app
# HOME: /app is read-only for uid 10001; each agent run gets its own HOME and Claude Code config
# directory inside its scratch directory (app/agents/runner.py), /tmp is a tmpfs (compose.yml).
# CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK: before each run the SDK would start `claude -v` with the
# worker's own, unfiltered environment (no env= in SubprocessCLITransport._check_claude_version,
# 0.2.163). The CLI is pinned by the wheel, so the probe checks it against itself; skipped, the
# only Claude Code process is the run's, started with the blanked environment (app/worker.py also
# sets it, and drops secrets from its own environment).
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1
WORKDIR /app
COPY --from=builder-worker --chown=root:root /app/.venv /app/.venv
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root alembic ./alembic
COPY --chown=root:root app ./app
USER app:app
HEALTHCHECK --interval=30s --timeout=8s --start-period=20s --retries=3 \
    CMD ["python", "-m", "app.worker", "--check"]
CMD ["python", "-m", "app.worker"]

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder --chown=root:root /app/.venv /app/.venv
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root alembic ./alembic
COPY --chown=root:root app ./app
USER app:app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]
# --limit-concurrency 256 (issue #100): once 256 connections are open, a connection counting as
# soon as it is open, uvicorn answers the next request 503. Measured once on a scratch stack:
# 260 slow connections cost about 42 MiB here, and while they were held every other request,
# the health check included, got 503. So this number bounds the memory callers with no session
# can take (a signed in user's bodies are bounded per user in app/body_limit.py, and password
# checks by their slots in app/identity/hash_slots.py, issue #108) and is also how many slow
# connections stop the api answering while they last; raising it trades one for the other
# (SPEC-platform.md, Request limits by layer). An event stream holds one connection, and so
# does the container's own health check, which reads unhealthy while the limit is reached.
# --timeout-keep-alive 5 keeps an idle keep-alive connection short (uvicorn's default, made
# explicit). Request headers are bounded in front, by caddy (about 20 KiB: its default of
# 16 KiB plus the 4,096 bytes Go adds): the api is reached from caddy only. The request body is bounded per route class by
# LimitRequestBodyMiddleware and, at the edge, by caddy/Caddyfile.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*", "--limit-concurrency", "256", "--timeout-keep-alive", "5"]
