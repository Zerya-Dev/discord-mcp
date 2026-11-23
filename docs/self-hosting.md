# Self-hosting

One bot process serves one Discord server: it keeps the index, answers `/mcp` commands and runs the MCP HTTP
server with its OAuth sign-in. Plan 15 minutes.

## 1. Create the Discord application

1. <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab → *Reset Token* and keep the token (`DISCORD_BOT_TOKEN`).
3. **Bot** tab → *Privileged Gateway Intents* → enable **Server Members Intent** and **Message Content Intent**.
   Without them the bot refuses to start (the log says which one is missing).
4. Copy the server ID (Discord → Settings → Advanced → Developer Mode, then right-click the server → *Copy
   Server ID*): `ZDM_GUILD_ID`.

You can invite the bot now or after the first start: the start-up log prints an invite link with exactly the
permissions it needs.

| Permission | Why |
|---|---|
| View Channels, Read Message History | index the channels the bot can see (channels it cannot see are simply not indexed) |
| Manage Threads | list *private* threads so they can be indexed (optional) |
| Send Messages, Send Messages in Threads | only with `ZDM_WRITE_ENABLED=true` |

To keep a channel out of the index entirely, deny the bot access to it on Discord or list it in
`ZDM_EXCLUDE_CHANNEL_IDS`.

## 2. Pick a public URL

claude.ai connects from Anthropic's cloud, so the server needs a public **HTTPS URL on port 443**, at the root of
its own (sub)domain, e.g. `https://mcp.example.com`. OAuth discovery lives at `/.well-known/…` of that origin and
the MCP endpoint at `/mcp`. Claude Code works with any URL it can reach (also `http://localhost`).

## 3. Run it

### Docker Compose

```yaml
services:
  discord-mcp:
    image: ghcr.io/zerya-dev/zerya-discord-mcp-bot:latest
    restart: unless-stopped
    environment:
      DISCORD_BOT_TOKEN: ${DISCORD_BOT_TOKEN}
      ZDM_GUILD_ID: "123456789012345678"
      ZDM_PUBLIC_URL: https://mcp.example.com
      ZDM_TIMEZONE: Europe/Warsaw
    volumes:
      - discord-mcp-data:/data
    ports:
      - "127.0.0.1:8000:8000"   # let only your reverse proxy reach it
volumes:
  discord-mcp-data:
```

Then put a reverse proxy with TLS in front of port 8000 for `mcp.example.com` (Caddy:
`mcp.example.com { reverse_proxy 127.0.0.1:8000 }`).

The image trusts `X-Forwarded-For` from any proxy (`FORWARDED_ALLOW_IPS=*`) because only the proxy should reach
the container; rate limits use the forwarded client IP. If the port is reachable from the internet directly, set
`FORWARDED_ALLOW_IPS` to your proxy's address.

### Dokploy

Create an *Application* from the image `ghcr.io/zerya-dev/zerya-discord-mcp-bot:latest` (or from this repo with
`packages/zerya-discord-mcp-bot/Dockerfile` and build context `.`), add the environment variables above, a
volume mounted at `/data`, and a domain on container port 8000 with HTTPS. Dokploy's Traefik terminates TLS.

### From source

```bash
uv sync --all-packages
cp .env.example .env    # fill in DISCORD_BOT_TOKEN, ZDM_GUILD_ID, ZDM_PUBLIC_URL
uv run start-app
```

## 4. Check the setup

On start the log prints a checklist, for example:

```text
setup: [OK] Bot is on the server — Zerya
setup: [OK] Permission: View Channels
setup: [WARN] Permission: Manage Threads (to index private threads) — grant it to the bot's role
setup: [OK] Public URL — https://mcp.example.com/mcp
```

Server admins see the same list on Discord with **`/mcp-setup`**. `GET /health` returns `200` with JSON once the
gateway, database and index are up (`503` otherwise), and the Docker image uses it as its health check.

The first start backfills the whole history (a few hundred messages per second, set by Discord rate limits);
search works during the backfill on what is indexed so far. Later starts only catch up.

## 5. Connect members

Each member connects their own client and only ever sees what they can see on Discord:

1. claude.ai → Settings → Connectors → *Add custom connector* → `https://mcp.example.com/mcp` → **Connect**
   (Claude Code: `claude mcp add --transport http discord https://mcp.example.com/mcp`, then `/mcp`).
2. A sign-in page opens. On Discord, run **`/mcp login`** and paste the code.
3. Done. `/mcp list` shows connected clients, `/mcp revoke` disconnects them.

On a claude.ai Team or Enterprise plan an owner adds the connector once; every member then connects it with their
own `/mcp login`.

## Operations

- **Backups:** back up the `/data` volume. The index can be rebuilt from Discord, but connected clients and the
  audit log cannot. SQLite in WAL mode: copy with `sqlite3 zerya-discord-mcp.sqlite3 ".backup backup.sqlite3"`.
- **Upgrades:** pull the new image and restart; migrations run on start.
- **Logs:** one line per tool call (`tool=search user=… grant=… results=… ms=…`); query strings are redacted from
  access logs. `LOG_LEVEL=DEBUG` for more.
- **Reset a member's access:** they run `/mcp revoke all`, or remove their role on Discord: access follows
  Discord permissions on every request.
