import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import hikari
import pytest

from zerya_discord_mcp import Config, ZeryaDiscordMcp
from zerya_discord_mcp.access.oauth import Grant
from zerya_discord_mcp.index import writer
from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord, MemberRecord, MessageRecord
from zerya_discord_mcp.query.directory import load_channels
from zerya_discord_mcp.testing.fixtures import GUILD_ID
from zerya_discord_mcp.tests import payloads

USER = 42
GENERAL, ADMIN, RANDOM, THREAD = 7, 8, 9, 70
VISIBLE_MESSAGE, HIDDEN_MESSAGE = 1 << 40, (1 << 40) + 1
SIGNATURE = "\n-# sent by an agent for Wiktor"


class Env:
    def __init__(self, core: ZeryaDiscordMcp, can_send: AsyncMock, create_message: AsyncMock) -> None:
        self.core = core
        self.can_send = can_send
        self.create_message = create_message

    async def send(
        self, content: str = "cześć", channel: str | None = None, reply_to: str | None = None, **kwargs: Any
    ) -> str:
        return await self.core.tools.send(USER, content, channel, reply_to, **kwargs)

    def audit(self) -> list[tuple[str, int | None, dict[str, Any]]]:
        rows = self.core.db.reader().execute("SELECT tool, result_count, args FROM audit_log ORDER BY id")
        return [(str(tool), count, json.loads(args)) for tool, count, args in rows]


async def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **config: Any) -> Env:
    intents = hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT | hikari.Intents.GUILD_MEMBERS
    bot = hikari.GatewayBot(token="MTIz.fake.token", intents=intents, banner=None)
    settings: dict[str, Any] = {"write_enabled": True} | config
    core = ZeryaDiscordMcp(bot, Config(db_path=tmp_path / "s.sqlite3", guild_id=GUILD_ID, **settings))
    core.db.open()
    channels = [
        ChannelRecord(GENERAL, "general", ChannelKind.TEXT),
        ChannelRecord(ADMIN, "admin", ChannelKind.TEXT),
        ChannelRecord(RANDOM, "random", ChannelKind.TEXT),
        ChannelRecord(THREAD, "wątek", ChannelKind.THREAD, parent_id=GENERAL),
    ]
    messages = [
        MessageRecord(id=VISIBLE_MESSAGE, channel_id=GENERAL, author_id=USER, content="pytanie"),
        MessageRecord(id=HIDDEN_MESSAGE, channel_id=ADMIN, author_id=USER, content="tajne"),
    ]
    core.db.write(lambda c: writer.upsert_channels(c, channels))
    core.db.write(lambda c: writer.upsert_messages(c, messages))
    core.db.write(lambda c: writer.upsert_members(c, [MemberRecord(USER, "wikipop", nick="Wiktor")]))
    core.sink.channels = load_channels(core.db.reader())
    core._loop = asyncio.get_running_loop()  # start() without a gateway

    async def allowed(user_id: int) -> frozenset[int] | None:
        return frozenset({GENERAL, RANDOM, THREAD}) if user_id == USER else None

    can_send = AsyncMock(return_value=True)
    create_message = AsyncMock(return_value=SimpleNamespace(id=555))
    monkeypatch.setattr(core, "allowed_channels", allowed)
    monkeypatch.setattr(core.access, "can_send", can_send)
    # RESTClientImpl has __slots__: patch the class (AsyncMock is not a descriptor, so no self is passed).
    monkeypatch.setattr(type(core.bot.rest), "create_message", create_message)
    return Env(core, can_send, create_message)


def _grant(*scopes: str) -> Grant:
    return Grant(
        id="g1",
        user_id=USER,
        client_id="client",
        client_name="Claude",
        scopes=scopes,
        created_at=0,
        last_used_at=None,
        expires_at=2**40,
        revoked_at=None,
    )


async def test_success_posts_safely_and_signs_the_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch)
    result = await env.send("hej <@1> @everyone https://example.com", channel="#general", grant=_grant("read", "write"))

    assert result == f"Sent: https://discord.com/channels/{GUILD_ID}/{GENERAL}/555"
    env.create_message.assert_awaited_once()
    assert env.create_message.await_args is not None
    args, kwargs = env.create_message.await_args
    assert args == (GENERAL, "hej <@1> @everyone https://example.com" + SIGNATURE)
    assert kwargs == {
        "reply": hikari.UNDEFINED,
        "flags": hikari.MessageFlag.SUPPRESS_EMBEDS,
        "mentions_everyone": False,
        "user_mentions": False,
        "role_mentions": False,
        "mentions_reply": False,
    }
    env.can_send.assert_awaited_once_with(USER, GENERAL, None)
    assert env.audit() == [("send", 1, {"channel": "#general", "chars": 38})]


async def test_posting_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch, write_enabled=False)
    result = await env.send(channel="#general")
    assert result == "Error: posting is disabled on this server."
    env.create_message.assert_not_awaited()
    assert env.audit() == [("send", 0, {"channel": "#general", "chars": 5, "error": result})]


async def test_read_only_grant_cannot_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch)
    result = await env.send(channel="#general", grant=_grant("read"))
    assert "read-only" in result
    env.create_message.assert_not_awaited()


@pytest.mark.parametrize("content", ["", "   \n", "x" * 1901])
async def test_content_length(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    env = await _env(tmp_path, monkeypatch)
    assert await env.send(content, channel="#general") == "Error: content must be 1–1900 characters."
    assert await env.send("x" * 1900, channel="#general") == f"Sent: https://discord.com/channels/{GUILD_ID}/7/555"


async def test_rate_limit_per_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch, send_rate_limit_per_minute=2)
    grant = _grant("read", "write")
    results = [await env.send(channel="#general", grant=grant) for _ in range(3)]
    assert [r.startswith("Sent:") for r in results] == [True, True, False]
    assert results[2] == "Error: too many messages, wait a minute before posting again."
    assert env.create_message.await_count == 2
    assert [row[1] for row in env.audit()] == [1, 1, 0]


async def test_reply_link_gives_the_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch)
    link = f"https://discord.com/channels/{GUILD_ID}/{GENERAL}/{VISIBLE_MESSAGE}"
    assert (await env.send("odpowiedź", reply_to=link)).startswith(f"Sent: https://discord.com/channels/{GUILD_ID}/7/")
    assert env.create_message.await_args is not None
    args, kwargs = env.create_message.await_args
    assert args[0] == GENERAL and kwargs["reply"] == VISIBLE_MESSAGE

    assert (await env.send("po ID", reply_to=str(VISIBLE_MESSAGE))).startswith("Sent:")
    mismatch = await env.send("x", channel="#random", reply_to=link)
    assert mismatch == "Error: reply_to points to a message in a different channel."
    assert await env.send("x", reply_to="nie link") == "Error: reply_to must be a message link or a message ID."
    assert await env.send("x") == "Error: Give a channel, or reply_to with a message link."


async def test_hidden_reply_target_looks_like_a_missing_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch)
    hidden = await env.send("x", reply_to=str(HIDDEN_MESSAGE))
    missing = await env.send("x", reply_to=str(HIDDEN_MESSAGE + 1000))
    hidden_link = await env.send("x", reply_to=f"https://discord.com/channels/{GUILD_ID}/{ADMIN}/{HIDDEN_MESSAGE}")
    assert hidden == missing == hidden_link == "Error: Unknown message to reply to. Pass its link."
    assert "Unknown channel" in await env.send("x", channel=str(ADMIN))
    env.create_message.assert_not_awaited()


async def test_allowlist_includes_threads_of_listed_channels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch, send_channel_ids=frozenset({GENERAL}))
    assert (await env.send(channel=str(THREAD))).startswith("Sent:")
    env.can_send.assert_awaited_once_with(USER, THREAD, GENERAL)  # threads are checked against their parent
    refused = await env.send(channel="#random")
    assert refused == "Error: posting is not allowed in this channel (it is not on the send allowlist)."
    assert env.create_message.await_count == 1


async def test_discord_permissions_decide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch)
    env.can_send.return_value = False
    result = await env.send(channel="#general")
    assert result.startswith("Error: you cannot post in this channel on Discord")
    env.create_message.assert_not_awaited()


async def test_people_off_the_server_cannot_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch)
    assert await env.core.tools.send(USER + 1, "x", "#general") == "Error: you are not a member of the server."


async def test_discord_errors_are_reported_and_audited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch)
    env.create_message.side_effect = payloads.forbidden()
    result = await env.send(channel="#general")
    assert result == "Error: Discord refused the message (403): Missing Access"
    assert env.audit() == [("send", 0, {"channel": "#general", "chars": 5, "error": result})]


async def test_every_attempt_is_audited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(tmp_path, monkeypatch)
    await env.send(channel="#general")
    await env.send(channel="#admin")
    await env.send("", channel="#general")
    audit = env.audit()
    assert [count for _, count, _ in audit] == [1, 0, 0]
    assert "error" not in audit[0][2]
    assert audit[1][2]["error"].startswith("Error: Unknown channel #admin")
    assert audit[2][2]["error"] == "Error: content must be 1–1900 characters."
