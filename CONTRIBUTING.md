# Contributing

Thanks for helping! Bugs and ideas go to [issues](https://github.com/Zerya-Dev/discord-mcp/issues);
security problems to [SECURITY.md](SECURITY.md).

```bash
uv sync --all-packages
just test lint typecheck   # must stay green: pytest, ruff + import-linter, pyrefly (strict)
just test-install          # packaging changes: the wheel with each extras combination, running the examples
```

- Search quality is the product. A change to `query/` or `index/` must not lower recall@10 on the eval set
  (`just eval`); include before/after numbers in the PR.
- Module boundaries are enforced by import-linter (`[tool.importlinter]` in `pyproject.toml`):
  - The core never imports the integrations (`arc/`, `mcp/`) or their dependencies, so it installs without extras.
  - The integrations do not import each other.
  - `index/` and `query/` never import hikari, so they are tested on SQLite alone.
- Public API: the names exported from `zerya_discord_mcp/__init__.py`, `zerya_discord_mcp/arc/__init__.py` and
  `zerya_discord_mcp/mcp/__init__.py`. Modules starting with `_` are internal.
- Every example in `examples/` is tested in-process and installed from the wheel; keep them working.
- Never weaken access control: channel filtering happens in SQL before ranking, and nothing (facets, counts,
  hints, mentions) may reveal channels the member cannot see.
