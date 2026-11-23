"""Assembles the library: index, ingest, access, query and the plain async tools."""

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import hikari

from zerya_discord_mcp.access.oauth import Grant, OAuthStore
from zerya_discord_mcp.access.permissions import AccessControl
from zerya_discord_mcp.commands import McpCommands
from zerya_discord_mcp.config import Config
from zerya_discord_mcp.diagnostics import SetupCheck, run_setup_checks
from zerya_discord_mcp.index.db import Database, check_sqlite
from zerya_discord_mcp.index.models import ChannelRecord
from zerya_discord_mcp.index.vocab import Vocabulary
from zerya_discord_mcp.ingest.backfill import Backfill
from zerya_discord_mcp.ingest.listeners import Listeners
from zerya_discord_mcp.ingest.sink import Sink
from zerya_discord_mcp.query.directory import Directory, load_channels, parse_link
from zerya_discord_mcp.query.info import Info
from zerya_discord_mcp.query.parser import QueryError
from zerya_discord_mcp.query.read import Reader
from zerya_discord_mcp.query.search import SearchEngine
from zerya_discord_mcp.ratelimit import RateLimiter
from zerya_discord_mcp.text import warm_up

if TYPE_CHECKING:
    from starlette.types import ASGIApp

    from zerya_discord_mcp.server.app import Authenticate

logger = logging.getLogger("zerya_discord_mcp")

REQUIRED_INTENTS = hikari.Intents.MESSAGE_CONTENT | hikari.Intents.GUILD_MEMBERS | hikari.Intents.GUILD_MESSAGES


class AccessDeniedError(Exception):
    """The user is not on the server (or the token lacks the needed scope)."""


class Tools:
    """The MCP tools as plain async functions. ``user_id`` is the Discord user the call acts for."""

    def __init__(self, core: ZeryaDiscordMcp) -> None:
        self._core = core
        config = core.config
        tz = ZoneInfo(config.timezone)
        self._search = SearchEngine(core.vocab, tz, config.guild_id)
        self._read = Reader(tz, config.guild_id)
        self._info = Info(tz, config.guild_id)
        self._send_limiter = RateLimiter(config.send_rate_limit_per_minute)

    async def _run(
        self,
        tool: str,
        user_id: int,
        grant: Grant | None,
        args: dict[str, Any],
        fn: Callable[[sqlite3.Connection, frozenset[int]], tuple[str, int | None]],
    ) -> str:
        started = time.monotonic()
        allowed = await self._core.allowed_channels(user_id)
        if allowed is None:
            raise AccessDeniedError("You are not a member of the server.")

        def call() -> tuple[str, int | None]:
            try:
                with self._core.db.snapshot() as conn:
                    return fn(conn, allowed)
            except QueryError as e:
                return f"Error: {e}", None

        text, count = await asyncio.to_thread(call)
        await self._core.audit(grant, user_id, tool, args, count, started)
        return text

    async def search(
        self,
        user_id: int,
        q: str,
        context: int | None = None,
        limit: int = 10,
        cursor: str | None = None,
        *,
        grant: Grant | None = None,
    ) -> str:
        config = self._core.config
        ctx = config.default_context if context is None else context

        def fn(conn: sqlite3.Connection, allowed: frozenset[int]) -> tuple[str, int | None]:
            result = self._search.search(
                conn, allowed, q, context=ctx, limit=limit, cursor=cursor, budget_tokens=config.search_budget_tokens
            )
            return result.text, result.hook_count

        args = {"q": q, "context": context, "limit": limit, "cursor": cursor}
        return await self._run("search", user_id, grant, args, fn)

    async def read(
        self,
        user_id: int,
        target: str | None = None,
        before: int | None = None,
        after: int | None = None,
        since: str | None = None,
        cursor: str | None = None,
        *,
        grant: Grant | None = None,
    ) -> str:
        budget = self._core.config.read_budget_tokens

        def fn(conn: sqlite3.Connection, allowed: frozenset[int]) -> tuple[str, int | None]:
            text = self._read.read(
                conn, allowed, target, before=before, after=after, since=since, cursor=cursor, budget_tokens=budget
            )
            return text, None

        args = {"target": target, "before": before, "after": after, "since": since, "cursor": cursor}
        return await self._run("read", user_id, grant, args, fn)

    async def info(self, user_id: int, target: str | None = None, *, grant: Grant | None = None) -> str:
        def fn(conn: sqlite3.Connection, allowed: frozenset[int]) -> tuple[str, int | None]:
            return self._info.info(conn, allowed, target), None

        return await self._run("info", user_id, grant, {"target": target}, fn)

    async def send(
        self,
        user_id: int,
        content: str,
        channel: str | None = None,
        reply_to: str | None = None,
        *,
        grant: Grant | None = None,
    ) -> str:
        """Posts as the bot. ``reply_to`` (message link or ID) alone is enough: the channel comes from it.

        Every attempt is audited, refused ones included.
        """
        core = self._core
        started = time.monotonic()
        args = {"channel": channel, "reply_to": reply_to, "chars": len(content)}
        key = grant.id if grant else f"user:{user_id}"
        if not self._send_limiter.allow(key):
            text, ok = "Error: too many messages, wait a minute before posting again.", False
        else:
            text, ok = await self._send(user_id, content, channel, reply_to, grant)
        await core.audit(grant, user_id, "send", args, 1 if ok else 0, started, error=None if ok else text)
        return text

    async def _send(
        self, user_id: int, content: str, channel: str | None, reply_to: str | None, grant: Grant | None
    ) -> tuple[str, bool]:
        core = self._core
        config = core.config
        if not config.write_enabled:
            return "Error: posting is disabled on this server.", False
        if grant is not None and not grant.can_write:
            return "Error: this connection is read-only. Reconnect the client and allow posting.", False
        if not content.strip() or len(content) > 1900:
            return "Error: content must be 1–1900 characters.", False
        allowed = await core.allowed_channels(user_id)
        if allowed is None:
            return "Error: you are not a member of the server.", False

        def resolve() -> tuple[int, int | None, str]:
            with core.db.snapshot() as conn:
                directory = Directory(conn, allowed)
                reply_id: int | None = None
                reply_channel: int | None = None
                if reply_to:
                    link = parse_link(reply_to)
                    if link and link.message_id:
                        reply_id, reply_channel = link.message_id, link.channel_id
                    elif reply_to.strip().isdigit():
                        reply_id = int(reply_to.strip())
                        row = conn.execute("SELECT channel_id FROM messages WHERE id = ?", (reply_id,)).fetchone()
                        reply_channel = int(row[0]) if row else None
                    else:
                        raise QueryError("reply_to must be a message link or a message ID.")
                    if reply_channel is None or reply_channel not in allowed:
                        # Same answer whether the message is missing or hidden: no probing other channels.
                        raise QueryError("Unknown message to reply to. Pass its link.")
                if channel:
                    channel_id = directory.resolve_channel(channel).id
                    if reply_channel is not None and reply_channel != channel_id:
                        raise QueryError("reply_to points to a message in a different channel.")
                elif reply_channel is not None:
                    channel_id = reply_channel
                else:
                    raise QueryError("Give a channel, or reply_to with a message link.")
                return channel_id, reply_id, directory.member_name(user_id)

        try:
            channel_id, reply_id, name = await asyncio.to_thread(resolve)
        except QueryError as e:
            return f"Error: {e}", False
        allowlist = config.send_channel_ids
        if allowlist and not any(cid in allowlist for cid in core.sink.lineage(channel_id)):
            return "Error: posting is not allowed in this channel (it is not on the send allowlist).", False
        record = core.sink.channels.get(channel_id)
        parent = record.parent_id if record is not None and record.kind.is_thread else None
        if not await core.on_bot_loop(lambda: core.access.can_send(user_id, channel_id, parent)):
            return (
                "Error: you cannot post in this channel on Discord (or you are timed out), so the agent cannot either.",
                False,
            )
        body = f"{content}\n-# sent by an agent for {name}"

        async def post() -> hikari.Message:
            return await core.bot.rest.create_message(
                channel_id,
                body,
                reply=reply_id if reply_id is not None else hikari.UNDEFINED,
                flags=hikari.MessageFlag.SUPPRESS_EMBEDS,
                mentions_everyone=False,
                user_mentions=False,
                role_mentions=False,
                mentions_reply=False,
            )

        try:
            message = await core.on_bot_loop(post)
        except hikari.HTTPResponseError as e:
            return f"Error: Discord refused the message ({e.status}): {e.message}", False
        return f"Sent: https://discord.com/channels/{config.guild_id}/{channel_id}/{message.id}", True


class ZeryaDiscordMcp:
    """Embeds Discord history search into an existing hikari bot.

    Create it before ``bot.run()`` (it subscribes to gateway events), call ``start()`` on StartedEvent and
    ``stop()`` on StoppingEvent, and mount ``asgi_app()`` in the host's HTTP server.
    """

    def __init__(self, bot: hikari.GatewayBot, config: Config) -> None:
        self.bot = bot
        self.config = config
        self.db = Database(config.db_path)
        self.vocab = Vocabulary()
        self.sink = Sink(bot, self.db, config)
        self.listeners = Listeners(self.sink)
        self.listeners.subscribe(bot)
        self.access = AccessControl(
            bot, self.db, config.guild_id, config.permission_cache_seconds, self.sink.is_indexed
        )
        self.sink.on_structure_change(self.access.invalidate)
        self.sink.on_member_change(self.access.invalidate_user)
        self.sink.on_thread_members_change(self.access.invalidate_thread)
        self.oauth = OAuthStore(
            self.db,
            access_ttl=config.access_token_ttl_seconds,
            refresh_ttl=config.refresh_token_ttl_days * 86400,
            login_ttl=config.login_code_ttl_seconds,
        )
        self.commands = McpCommands(self.oauth, config, self._is_member, self.setup_report, self.server_name)
        self.tools = Tools(self)
        self.backfill: Backfill | None = None
        self._maintenance_task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._ready_count = 0
        self.status: dict[str, Any] = {"last_sync": None, "last_error": None}
        bot.subscribe(hikari.ShardReadyEvent, self._on_shard_ready)

    def server_name(self) -> str:
        """Config.server_name, else the Discord server's name, else "Discord"."""
        if self.config.server_name:
            return self.config.server_name
        guild = self.bot.cache.get_guild(self.config.guild_id)
        return guild.name if guild is not None else "Discord"

    def _is_member(self, user_id: int) -> bool:
        return self.bot.cache.get_member(self.config.guild_id, user_id) is not None

    async def start(self, *, backfill: bool = True) -> None:
        missing = REQUIRED_INTENTS & ~self.bot.intents
        if missing:
            raise RuntimeError(f"zerya-discord-mcp needs these gateway intents: {missing!r}")
        check_sqlite()
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self.db.open)
        await asyncio.to_thread(warm_up)
        self.sink.channels = await asyncio.to_thread(self._load_channels)
        self.listeners.ready = True
        self._started = True
        if backfill:
            self._maintenance_task = asyncio.create_task(self._maintain(), name="zdm-maintenance")
        logger.info("zerya-discord-mcp started (db %s)", self.config.db_path)

    async def _on_shard_ready(self, _event: hikari.ShardReadyEvent) -> None:
        # A new gateway session (not a resume) may have missed events: catch up and reconcile right away.
        self._ready_count += 1
        if self._started and self._ready_count > 1:
            logger.info("Gateway session restarted, scheduling catch-up")
            self._wake.set()

    async def _maintain(self) -> None:
        """Backfill, then every maintenance interval: catch up, reconcile recent history, housekeeping.

        Failures are logged and retried with backoff; the loop never dies silently.
        """
        delay = 30.0
        while True:
            try:
                backfill = Backfill(self.sink)
                self.backfill = backfill
                await backfill.run()
                await backfill.reconcile(self.config.reconcile_days)
                await asyncio.to_thread(self._housekeeping)
                self.vocab.invalidate()
                self.status.update(last_sync=int(time.time()), last_error=None)
                delay = 30.0
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.config.maintenance_interval_minutes * 60)
                except TimeoutError:
                    pass
                self._wake.clear()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.status["last_error"] = f"{type(e).__name__}: {e}"
                logger.exception("Index maintenance failed, retrying in %.0fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 900.0)

    def _housekeeping(self) -> None:
        pruned = self.oauth.housekeeping()
        cutoff = int(time.time()) - self.config.audit_retention_days * 86400
        pruned["audit"] = self.db.write(
            lambda conn: conn.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,)).rowcount
        )
        if any(pruned.values()):
            logger.info("Housekeeping removed %s", {k: v for k, v in pruned.items() if v})

    def _load_channels(self) -> dict[int, ChannelRecord]:
        with self.db.snapshot() as conn:
            return load_channels(conn)

    async def stop(self) -> None:
        self.listeners.ready = False
        self._started = False
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
        await asyncio.to_thread(self.db.close)

    def health(self) -> dict[str, Any]:
        """Liveness for /health: started, database open and the gateway connected."""
        gateway = self.bot.is_alive and all(
            shard.is_alive and shard.heartbeat_latency != float("inf") for shard in self.bot.shards.values()
        )
        backfill = self.backfill
        ok = self._started and self.db.is_open and gateway
        return {
            "ok": ok,
            "gateway": gateway,
            "database": self.db.is_open,
            "backfill": {
                "done": backfill.done.is_set() if backfill else False,
                "messages": backfill.indexed_messages if backfill else 0,
                "failed_channels": backfill.failed_channels if backfill else 0,
            },
            **self.status,
        }

    async def setup_report(self) -> list[SetupCheck]:
        """Checks a server owner can act on: guild membership, bot permissions, public URL, invite link."""
        return await self.on_bot_loop(lambda: run_setup_checks(self))

    async def on_bot_loop[T](self, fn: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """Runs a coroutine on the bot's event loop (hikari REST and cache live there)."""
        loop = self._loop
        if loop is None:
            raise RuntimeError("zerya-discord-mcp is not started")
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            return await fn()
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(fn(), loop))

    async def allowed_channels(self, user_id: int) -> frozenset[int] | None:
        return await self.on_bot_loop(lambda: self.access.allowed_channels(user_id))

    async def audit(
        self,
        grant: Grant | None,
        user_id: int,
        tool: str,
        args: dict[str, Any],
        result_count: int | None,
        started: float,
        *,
        error: str | None = None,
    ) -> None:
        duration = int((time.monotonic() - started) * 1000)
        payload = {k: v for k, v in args.items() if v is not None}
        if error:
            payload["error"] = error
        text = json.dumps(payload, ensure_ascii=False)
        logger.info(
            "tool=%s user=%s grant=%s results=%s ms=%s%s",
            tool,
            user_id,
            grant.id if grant else "-",
            result_count if result_count is not None else "-",
            duration,
            f" error={error!r}" if error else "",
        )

        def write(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO audit_log (grant_id, user_id, tool, args, result_count, duration_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (grant.id if grant else None, user_id, tool, text, result_count, duration, int(time.time())),
            )

        try:
            await asyncio.to_thread(self.db.write, write)
        except sqlite3.Error:
            logger.exception("Audit log write failed")

    def asgi_app(self, authenticate: Authenticate | None = None) -> ASGIApp:
        """MCP over streamable HTTP with its own OAuth server, at ``config.public_url``. Needs the ``[mcp]`` extra.

        Mount it at the root of that origin (OAuth discovery uses ``/.well-known/`` paths); the MCP endpoint is
        ``/mcp``, the sign-in page ``/login``.
        """
        from zerya_discord_mcp.server.app import build_app

        return build_app(self, authenticate)
