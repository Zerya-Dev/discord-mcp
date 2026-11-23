from datetime import date
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Library settings. Every field can come from the environment with the ``ZDM_`` prefix."""

    model_config = SettingsConfigDict(env_prefix="ZDM_", extra="ignore", frozen=True)

    db_path: Path
    guild_id: int

    # Index scope. Excluding a channel also excludes its threads; it never enters the index at all.
    index_channel_ids: frozenset[int] = frozenset()  # empty = every channel the bot can read
    exclude_channel_ids: frozenset[int] = frozenset()
    backfill_since: date | None = None  # None = the whole history
    backfill_concurrency: int = 3
    # Every maintenance_interval_minutes: catch up, then re-read the last reconcile_days days so edits and
    # deletes made while the bot was offline (or during a gateway reconnect) reach the index.
    maintenance_interval_minutes: int = 360
    reconcile_days: int = 7

    # Writing. send exists only when write_enabled and only for connections the user approved with posting.
    # The agent can post only where the user can post on Discord; send_channel_ids narrows that further
    # (a channel includes its threads). Empty = every channel the user can post in.
    write_enabled: bool = False
    send_channel_ids: frozenset[int] = frozenset()
    send_rate_limit_per_minute: int = 5

    # Access (OAuth). Access tokens are short; refresh tokens rotate and end after this many idle days.
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_days: int = 90
    login_code_ttl_seconds: int = 600
    permission_cache_seconds: float = 60.0
    rate_limit_per_minute: int = 60
    # Hosts OAuth clients may redirect to after sign-in (e.g. ["claude.ai", "localhost", "127.0.0.1"]).
    # Empty = any host; the sign-in page always shows where the code goes.
    oauth_redirect_hosts: frozenset[str] = frozenset()
    audit_retention_days: int = 90

    # Output.
    timezone: str = "UTC"  # time zone of timestamps in tool output, e.g. Europe/Warsaw
    default_context: int = 5
    search_budget_tokens: int = 4000
    read_budget_tokens: int = 6000
    # Origin the HTTP app is served at, e.g. https://mcp.zerya.dev (OAuth issuer; MCP endpoint is {public_url}/mcp).
    # Required for asgi_app(). Mount the app at the root of this origin: OAuth discovery uses /.well-known/ paths.
    public_url: str | None = None
    # Name shown on the sign-in page, in /mcp texts and to agents. Default: the Discord server's name.
    server_name: str | None = None
