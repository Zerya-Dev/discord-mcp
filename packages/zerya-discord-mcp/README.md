# zerya-discord-mcp

**Permission-aware search over your whole Discord history, for Claude and other AI agents.**

The library behind [zerya-discord-mcp](https://github.com/Zerya-Dev/zerya-discord-mcp): embed it in a
[hikari](https://github.com/hikari-py/hikari) bot to get a SQLite full-text index of your server, a query engine
that returns context "hooks", live per-member Discord permissions, and a remote MCP server with its own OAuth 2.1
sign-in (`/mcp login`). Works as a claude.ai custom connector and in Claude Code.

```bash
pip install "zerya-discord-mcp[mcp]"
```

```python
from zerya_discord_mcp import Config, ZeryaDiscordMcp

zdm = ZeryaDiscordMcp(
    bot, Config(db_path=Path("/data/zdm.sqlite3"), guild_id=GUILD_ID, public_url="https://mcp.example.com")
)
# StartedEvent: await zdm.start()   StoppingEvent: await zdm.stop()
app = zdm.asgi_app()  # ASGI app to serve at the root of public_url
```

Prefer a ready-made bot? Use the Docker image `ghcr.io/zerya-dev/zerya-discord-mcp-bot`. Documentation, security model and configuration:
<https://github.com/Zerya-Dev/zerya-discord-mcp>.
