default: dev

format:
  uv run ruff check --fix && uv format

lint:
  uv run ruff check && uv format --check

typecheck:
  uv run pyrefly check

test:
  uv run pytest

# Standalone bot (gateway + /mcp commands + MCP HTTP server)
dev:
  uv run start-app

# Index only: no slash command sync, safe to run with a token another bot uses
index:
  ZDM_BOT_COMMANDS=false uv run start-app

# Query a local index as an admin, e.g. just search "(logo|logotyp) fundacja"
search query:
  uv run zdm-dev search "{{query}}"

eval file="eval/questions.yaml":
  uv run zdm-dev eval {{file}}

install:
  uv sync --all-packages
