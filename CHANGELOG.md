# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-23

First public release, distributed as the Docker image `ghcr.io/zerya-dev/zerya-discord-mcp-bot`; the
library installs from the git tag.

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
