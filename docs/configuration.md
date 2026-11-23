# Configuration

Environment variables (a `.env` file in the working directory is read too). Lists are JSON:
`ZDM_EXCLUDE_CHANNEL_IDS=[123,456]`.

## Required

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Bot token (standalone bot only). |
| `ZDM_GUILD_ID` | The one server this instance indexes and serves. |
| `ZDM_DB_PATH` | SQLite file of the index. Docker image: `/data/zerya-discord-mcp.sqlite3`. |
| `ZDM_PUBLIC_URL` | Origin of the HTTP app without a path, e.g. `https://mcp.example.com`. OAuth issuer; the MCP endpoint is `{ZDM_PUBLIC_URL}/mcp`. Required when serving HTTP. |

## Index

| Variable | Default | Description |
|---|---|---|
| `ZDM_INDEX_CHANNEL_IDS` | `[]` | Only index these channels (and their threads). Empty = every channel the bot can read. |
| `ZDM_EXCLUDE_CHANNEL_IDS` | `[]` | Never index these channels (and their threads). |
| `ZDM_BACKFILL_SINCE` | whole history | Oldest date to backfill, e.g. `2024-01-01`. |
| `ZDM_BACKFILL_CONCURRENCY` | `3` | Channels fetched in parallel during backfill. |
| `ZDM_MAINTENANCE_INTERVAL_MINUTES` | `360` | How often to catch up, reconcile and clean up. A gateway reconnect triggers it immediately. |
| `ZDM_RECONCILE_DAYS` | `7` | Window re-read on each maintenance run to apply edits and deletions missed while offline. |

## Access and security

| Variable | Default | Description |
|---|---|---|
| `ZDM_ACCESS_TOKEN_TTL_SECONDS` | `3600` | OAuth access token lifetime. |
| `ZDM_REFRESH_TOKEN_TTL_DAYS` | `90` | Refresh tokens rotate; a connection ends after this many days without use. |
| `ZDM_LOGIN_CODE_TTL_SECONDS` | `600` | Lifetime of `/mcp login` codes (single use). |
| `ZDM_OAUTH_REDIRECT_HOSTS` | `[]` (any) | Hosts clients may redirect to after sign-in, e.g. `["claude.ai","localhost","127.0.0.1"]`. |
| `ZDM_PERMISSION_CACHE_SECONDS` | `60` | How long a member's readable channels are cached (dropped on role/channel/member changes). |
| `ZDM_RATE_LIMIT_PER_MINUTE` | `60` | MCP requests per connection. |
| `ZDM_AUDIT_RETENTION_DAYS` | `90` | Audit log retention. |

## Writing

| Variable | Default | Description |
|---|---|---|
| `ZDM_WRITE_ENABLED` | `false` | Enables the `send` tool and the `write` OAuth scope. |
| `ZDM_SEND_CHANNEL_IDS` | `[]` (anywhere the member may post) | Optional allowlist; a channel includes its threads. |
| `ZDM_SEND_RATE_LIMIT_PER_MINUTE` | `5` | Messages per connection. |

## Output

| Variable | Default | Description |
|---|---|---|
| `ZDM_TIMEZONE` | `UTC` | Time zone of timestamps in tool output, e.g. `Europe/Warsaw`. |
| `ZDM_SERVER_NAME` | the Discord server's name | Name shown on the sign-in page, in `/mcp` texts and to agents. |
| `ZDM_DEFAULT_CONTEXT` | `5` | Messages shown before/after each search hit. |
| `ZDM_SEARCH_BUDGET_TOKENS` | `4000` | Approximate size of one search page. |
| `ZDM_READ_BUDGET_TOKENS` | `6000` | Approximate size of one read page. |

## Standalone bot

| Variable | Default | Description |
|---|---|---|
| `HTTP_HOST` / `HTTP_PORT` | `0.0.0.0` / `8000` | Where the HTTP server listens. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` (`*` in Docker) | Proxies whose `X-Forwarded-For` is trusted. |
| `LOG_LEVEL` | `INFO` | |
| `ZDM_BOT_COMMANDS` | `true` | `false` = no slash commands (pure indexer and MCP server). |
| `ZDM_BOT_BULK_SYNC` | `true` | `false` = upsert only `/mcp` and `/mcp-setup` in the guild instead of syncing all application commands (use when the bot token has other commands). |
| `ZDM_BOT_HTTP` | `true` | `false` = no HTTP server (index only). |
