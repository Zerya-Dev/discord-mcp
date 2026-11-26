# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

The library is split into a framework-neutral core and two optional integrations, each with one public module.
Breaking changes are listed under Changed and Removed.

### Added

- `zerya_discord_mcp.arc.McpPlugin` (`[arc]` extra): the library as an arc plugin. It starts the library on
  `StartedEvent` (and logs the setup checklist), stops it on `StoppingEvent`, and adds `/mcp login | list | revoke`
  and `/mcp-setup`. `manage_lifecycle=False` leaves start and stop to the host.
- `zerya_discord_mcp.mcp.McpAsgiApp` (`[mcp]` extra), with `Principal` and `Authenticate` for custom sign-in.
- `command_builders()` and `upsert_commands()` (REST command definitions) and `McpCommands.handle()` (answers a raw
  `CommandInteraction`), for hosts without a command framework.
- `ZeryaDiscordMcp.log_setup_report()`, `ZeryaDiscordMcp.add_housekeeping()`, and `REQUIRED_INTENTS`, `Grant`,
  `SetupCheck` and `McpCommands` exported from `zerya_discord_mcp`.
- Importing an integration without its extra fails with the `pip install` command to run.
- [examples/](examples/) with three tested programs: arc, plain hikari, and the core alone.

### Changed

- **Breaking:** `ZeryaDiscordMcp.asgi_app(authenticate)` is now `McpAsgiApp(zdm, authenticate)`.
- **Breaking:** `McpCommands.setup(permissions, locale)` takes the member's permissions and refuses members without
  Manage Server, in English or Polish, so every host checks them the same way.
- **Breaking:** `ZeryaDiscordMcp.oauth` is gone. Sign-in codes and grants are in `ZeryaDiscordMcp.grants`; tokens
  and OAuth clients belong to the MCP app.
- `ZeryaDiscordMcp.start()` raises when the library is already started, instead of opening the database twice.

### Fixed

- `pip install zerya-discord-mcp` without the `[mcp]` extra failed on `import zerya_discord_mcp` (the core imported
  the MCP SDK). The core no longer depends on any integration; CI enforces the module boundaries with
  import-linter and installs the wheel with each combination of extras.

## [1.0.0] - 2026-11-23

First public release: the bot as the Docker image `ghcr.io/zerya-dev/zerya-discord-mcp-bot`, the library on PyPI
as `zerya-discord-mcp`.

### Added

- SQLite index of a Discord server: resumable backfill, live create/edit/delete listeners, threads and forum
  posts, periodic reconciliation of edits and deletions made while offline.
- Query engine: query language (`(a|b)`, `"phrase"`, `-exclude`, `in:`, `from:`, `mentions:`, `after:`,
  `before:`, `has:`, `is:thread`), FTS5 words + lemmas (Polish, English), trigram fragments, typo variants from the
  corpus, hooks with surrounding messages, facets, token budgets, hints when nothing matches.
- MCP tools `search`, `read`, `info` and optional `send` over streamable HTTP.
- OAuth 2.1 authorization server with dynamic client registration, PKCE, rotating refresh tokens and reuse
  detection; sign-in with a one-time `/mcp login` code.
- Live per-member Discord permissions (roles, channel overwrites, private thread membership) on every request.
- Slash commands `/mcp login | list | revoke` and `/mcp-setup`, in English and Polish.
- Standalone bot with Docker image, health check and setup checklist; embeddable library for hikari bots.
