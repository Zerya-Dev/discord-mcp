"""Standalone bot: gateway connection, /mcp commands and the MCP HTTP server in one process."""

import asyncio
import logging
import re
from typing import override

import arc
import hikari
import uvicorn
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from zerya_discord_mcp import Config, ZeryaDiscordMcp
from zerya_discord_mcp_bot.commands import build_plugin, upsert_mcp_command

logger = logging.getLogger("zerya_discord_mcp_bot")


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_bot_token: str
    http_host: str = "0.0.0.0"
    http_port: int = 8000
    # Proxies whose X-Forwarded-For is trusted (rate limits use the client IP). "*" when only a reverse proxy
    # (Traefik/Dokploy, Caddy, nginx) can reach the container; the Docker image sets it.
    forwarded_allow_ips: str = "127.0.0.1"
    log_level: str = "INFO"
    # False: no slash commands at all (pure indexer + MCP server).
    zdm_bot_commands: bool = True
    # False: do not bulk-sync application commands (arc overwrites every global and guild command of the
    # application). Use it when the bot token also runs other commands; only /mcp is upserted in the guild.
    zdm_bot_bulk_sync: bool = True
    zdm_bot_http: bool = True


class _RedactQuery(logging.Filter):
    """Access logs keep paths but drop query strings (OAuth state, PKCE challenges, sign-in request IDs)."""

    _query = re.compile(r"\?[^ \"]*")

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._query.sub("?…", a) if isinstance(a, str) else a for a in record.args)
        return True


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("uvicorn.access").addFilter(_RedactQuery())
    # Stateless streamable HTTP "terminates" a session per request; that is noise.
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)


def http_app(zdm: ZeryaDiscordMcp) -> Starlette:
    """/health plus the MCP app, which owns the origin root (OAuth discovery needs /.well-known/ paths there)."""

    async def health(_: Request) -> JSONResponse:
        status = zdm.health()
        return JSONResponse(status, status_code=200 if status["ok"] else 503)

    return Starlette(routes=[Route("/health", health), Mount("/", app=zdm.asgi_app())])


def main() -> None:
    settings = BotSettings()
    configure_logging(settings.log_level)
    config = Config(_env_file=".env")

    intents = hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT | hikari.Intents.GUILD_MEMBERS
    bot = hikari.GatewayBot(token=settings.discord_bot_token, intents=intents, banner=None)
    zdm = ZeryaDiscordMcp(bot, config)

    if settings.zdm_bot_commands:
        client = arc.GatewayClient(bot, default_enabled_guilds=[config.guild_id], autosync=settings.zdm_bot_bulk_sync)
        client.add_plugin(build_plugin(zdm))
    else:
        logger.info("Slash commands disabled (ZDM_BOT_COMMANDS=false)")

    server: uvicorn.Server | None = None
    server_task: asyncio.Task[None] | None = None

    @bot.listen(hikari.StartedEvent)
    async def on_started(_: hikari.StartedEvent) -> None:
        nonlocal server, server_task
        try:
            await zdm.start()
            if settings.zdm_bot_commands and not settings.zdm_bot_bulk_sync:
                await upsert_mcp_command(bot, config.guild_id)
            for line in await zdm.setup_report():
                logger.log(logging.INFO if line.ok else logging.WARNING, "setup: %s", line)
        except Exception:
            # Fail fast: a half-started process with a green health check is worse than a restart loop.
            logger.exception("Startup failed, shutting down")
            await bot.close()
            return
        if settings.zdm_bot_http:
            server = uvicorn.Server(
                uvicorn.Config(
                    http_app(zdm),
                    host=settings.http_host,
                    port=settings.http_port,
                    log_level=settings.log_level.lower(),
                    lifespan="on",
                    proxy_headers=True,
                    forwarded_allow_ips=settings.forwarded_allow_ips,
                )
            )
            server_task = asyncio.create_task(server.serve(), name="zdm-http")

    @bot.listen(hikari.StoppingEvent)
    async def on_stopping(_: hikari.StoppingEvent) -> None:
        if server is not None:
            server.should_exit = True
        if server_task is not None:
            await server_task
        await zdm.stop()

    bot.run()


if __name__ == "__main__":
    main()
