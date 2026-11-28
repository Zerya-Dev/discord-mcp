# syntax=docker/dockerfile:1@sha256:572cebbc2b15e6f84c077742952674889cb63cd5e9b80abdffcbe587b6f19183
# Build from the repository root: docker build -f packages/zerya-discord-mcp-bot/Dockerfile .

FROM python:3.14-slim-trixie@sha256:f57c4c23b985d95deea5bc5249e2f9c8a512b4cc9d5d3fd31900cbd2f5f0a604 AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_DEFAULT_GROUPS=1

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock,readonly \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml,readonly \
    --mount=type=bind,source=packages/zerya-discord-mcp/pyproject.toml,target=packages/zerya-discord-mcp/pyproject.toml,readonly \
    --mount=type=bind,source=packages/zerya-discord-mcp-bot/pyproject.toml,target=packages/zerya-discord-mcp-bot/pyproject.toml,readonly \
    uv sync --locked --package zerya-discord-mcp-bot --no-install-workspace --no-editable

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package zerya-discord-mcp-bot --no-editable

FROM python:3.14-slim-trixie@sha256:f57c4c23b985d95deea5bc5249e2f9c8a512b4cc9d5d3fd31900cbd2f5f0a604 AS runtime

RUN groupadd --system --gid 10001 nonroot \
 && useradd --system \
      --uid 10001 \
      --gid 10001 \
      --create-home \
      --no-log-init \
      nonroot \
 && mkdir -p /data && chown 10001:10001 /data

WORKDIR /app

COPY --from=build --chown=10001:10001 /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ZDM_DB_PATH=/data/zerya-discord-mcp.sqlite3 \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8000 \
    FORWARDED_ALLOW_IPS=*

VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status != 200)"
USER 10001:10001

ENTRYPOINT ["start-app"]
