# Contributing

Thanks for helping! Bugs and ideas go to [issues](https://github.com/Zerya-Dev/discord-mcp/issues);
security problems to [SECURITY.md](SECURITY.md).

```bash
uv sync --all-packages
just test lint typecheck   # must stay green: pytest, ruff, pyrefly (strict)
```

- Search quality is the product. A change to `query/` or `index/` must not lower recall@10 on the eval set
  (`just eval`); include before/after numbers in the PR.
- `index/` and `query/` never import hikari, so they are tested on SQLite alone.
- Only names exported from `zerya_discord_mcp/__init__.py` are public API.
- Never weaken access control: channel filtering happens in SQL before ranking, and nothing (facets, counts,
  hints, mentions) may reveal channels the member cannot see.
