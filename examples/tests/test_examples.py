"""Every example builds and works in-process, against this checkout of the library."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import arc
import hikari
import httpx2 as httpx

from examples.tests.examples import load
from zerya_discord_mcp import Config, ZeryaDiscordMcp
from zerya_discord_mcp.index import writer
from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord, MessageRecord

TOKEN = "MTIz.fake.token"  # well-formed; the bots are never started
ORIGIN = "https://mcp.example.com"
GUILD = 1000
MEMBER, STRANGER = 42, 43


def _config(tmp_path: Path) -> Config:
    return Config(db_path=tmp_path / "zdm.sqlite3", guild_id=GUILD, public_url=ORIGIN)


async def _serves_oauth_discovery(app: Any) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as http:
        metadata = await http.get("/.well-known/oauth-authorization-server")
    assert metadata.status_code == 200
    assert metadata.json()["issuer"].rstrip("/") == ORIGIN


async def test_arc_bot_adds_the_plugin_and_serves_mcp(tmp_path: Path) -> None:
    bot, client, app = load("arc_bot").build(TOKEN, _config(tmp_path))

    assert isinstance(bot, hikari.GatewayBot) and isinstance(client, arc.GatewayClient)
    commands = {c.name for c in client.walk_commands(hikari.CommandType.SLASH)}
    assert commands == {"mcp", "login", "list", "revoke", "mcp-setup"}
    assert bot.get_listeners(hikari.StartedEvent) and bot.get_listeners(hikari.StoppingEvent)
    await _serves_oauth_discovery(app)


async def test_hikari_bot_answers_its_commands_and_serves_mcp(tmp_path: Path) -> None:
    bot, app = load("hikari_bot").build(TOKEN, _config(tmp_path))

    assert bot.get_listeners(hikari.StartedEvent) and bot.get_listeners(hikari.StoppingEvent)
    await _serves_oauth_discovery(app)

    interaction = Mock(spec=hikari.CommandInteraction)
    interaction.command_name = "mcp"
    interaction.locale = hikari.Locale.EN_US
    interaction.user = Mock(spec=hikari.User, id=hikari.Snowflake(MEMBER))
    interaction.options = [
        hikari.CommandInteractionOption(name="login", type=hikari.OptionType.SUB_COMMAND, value=None, options=None)
    ]
    interaction.create_initial_response = AsyncMock()
    event = Mock(spec=hikari.InteractionCreateEvent, interaction=interaction)

    for listener in bot.get_listeners(hikari.InteractionCreateEvent):
        await listener(event)

    interaction.create_initial_response.assert_awaited_once()
    call = interaction.create_initial_response.await_args
    assert call is not None
    kwargs = call.kwargs
    assert kwargs["flags"] == hikari.MessageFlag.EPHEMERAL
    assert kwargs["embed"].title == "No access"  # the member is not in the (empty) cache of a bot never started


async def test_core_only_answers_searches_with_what_the_member_can_read(tmp_path: Path) -> None:
    example = load("core_only")
    _, zdm = example.build(TOKEN, _config(tmp_path))
    _index_one_message(zdm, "ustalamy logo fundacji")

    reply = await example.answer(zdm, MEMBER, "!search logo fundacja")
    assert reply is not None and "ustalamy logo fundacji" in reply
    assert await example.answer(zdm, MEMBER, "hello") is None
    assert await example.answer(zdm, STRANGER, "!search logo") == "You are not a member of the server."


def _index_one_message(zdm: ZeryaDiscordMcp, content: str) -> None:
    """An index with one channel only MEMBER can read, as if the bot had started and backfilled."""
    zdm.db.open()
    zdm.db.write(lambda c: writer.upsert_channels(c, [ChannelRecord(id=7, name="general", kind=ChannelKind.TEXT)]))
    zdm.db.write(
        lambda c: writer.upsert_messages(
            c, [MessageRecord(id=1 << 40, channel_id=7, author_id=MEMBER, content=content)]
        )
    )
    zdm._loop = asyncio.get_running_loop()  # start() without a gateway

    async def allowed(user_id: int) -> frozenset[int] | None:
        return frozenset({7}) if user_id == MEMBER else None

    zdm.allowed_channels = allowed
