# zerya-discord-mcp-bot

Self-hosted Discord bot that serves [zerya-discord-mcp](https://github.com/Zerya-Dev/zerya-discord-mcp):
permission-aware search over your whole Discord history for Claude and other AI agents, as a remote MCP server
with Discord sign-in.

```bash
pip install zerya-discord-mcp-bot
DISCORD_BOT_TOKEN=... ZDM_GUILD_ID=... ZDM_PUBLIC_URL=https://mcp.example.com ZDM_DB_PATH=./zdm.sqlite3 start-app
```

Or with Docker: `ghcr.io/zerya-dev/zerya-discord-mcp-bot`. Setup guide:
<https://github.com/Zerya-Dev/zerya-discord-mcp/blob/main/docs/self-hosting.md>.
