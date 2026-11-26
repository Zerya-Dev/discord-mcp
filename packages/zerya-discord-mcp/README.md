# zerya-discord-mcp

**Permission-aware search over your whole Discord history, for Claude and other AI agents.**

The library behind [zerya-discord-mcp](https://github.com/Zerya-Dev/discord-mcp): embed it in a
[hikari](https://github.com/hikari-py/hikari) bot to get a SQLite full-text index of your server, a query engine
that returns context "hooks", live per-member Discord permissions, and a remote MCP server with its own OAuth 2.1
sign-in (`/mcp login`). Works as a claude.ai custom connector and in Claude Code.

```bash
pip install "zerya-discord-mcp[arc,mcp]"   # the core alone: pip install zerya-discord-mcp
```

```python
from zerya_discord_mcp import REQUIRED_INTENTS, Config, ZeryaDiscordMcp
from zerya_discord_mcp.arc import McpPlugin  # [arc] extra
from zerya_discord_mcp.mcp import McpAsgiApp  # [mcp] extra

bot = hikari.GatewayBot(token, intents=hikari.Intents.ALL_UNPRIVILEGED | REQUIRED_INTENTS)
zdm = ZeryaDiscordMcp(bot, Config())  # settings from ZDM_* environment variables
arc_client.add_plugin(McpPlugin(zdm))  # starts and stops with the bot, adds /mcp and /mcp-setup
app = McpAsgiApp(zdm)  # ASGI app to serve at the root of ZDM_PUBLIC_URL
```

Prefer a ready-made bot? Use the Docker image `ghcr.io/zerya-dev/zerya-discord-mcp-bot`. Documentation, security model and configuration:
<https://github.com/Zerya-Dev/discord-mcp>.
