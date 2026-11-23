# zerya-discord-mcp-bot

Self-hosted Discord bot that serves [zerya-discord-mcp](https://github.com/Zerya-Dev/discord-mcp):
permission-aware search over your whole Discord history for Claude and other AI agents, as a remote MCP server
with Discord sign-in.

```bash
docker run -e DISCORD_BOT_TOKEN=... -e ZDM_GUILD_ID=... -e ZDM_PUBLIC_URL=https://mcp.example.com \
  -v zdm-data:/data -p 8000:8000 ghcr.io/zerya-dev/zerya-discord-mcp-bot:1.0.0
``` Setup guide:
<https://github.com/Zerya-Dev/discord-mcp/blob/main/docs/self-hosting.md>.
