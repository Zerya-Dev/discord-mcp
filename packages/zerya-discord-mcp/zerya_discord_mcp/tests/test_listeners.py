from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import hikari
import pytest

from zerya_discord_mcp.config import Config
from zerya_discord_mcp.index.db import Database
from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord
from zerya_discord_mcp.ingest.listeners import Listeners
from zerya_discord_mcp.ingest.sink import Sink
from zerya_discord_mcp.testing.fixtures import GUILD_ID
from zerya_discord_mcp.tests import payloads as p

GENERAL, EXCLUDED, FORUM = 10, 11, 13
OLA = p.user(1, "ola", global_name="Ola")
SHARD = cast(hikari.api.GatewayShard, Mock())
APP = cast(hikari.RESTAware, Mock())


class FakeCache:
    def __init__(self) -> None:
        self.channels: dict[int, hikari.GuildChannel] = {}

    def get_guild_channel(self, channel_id: int) -> hikari.GuildChannel | None:
        return self.channels.get(channel_id)

    def get_member(self, guild: int, user: int) -> None:
        return None

    def get_user(self, user: int) -> None:
        return None

    def get_role(self, role: int) -> None:
        return None


class FakeBot:
    """Records gateway subscriptions and dispatches events to them like hikari's event manager."""

    def __init__(self) -> None:
        self.cache = FakeCache()
        self.rest = Mock()
        self.handlers: list[tuple[type[hikari.Event], Callable[[Any], Awaitable[None]]]] = []

    def subscribe(self, event_type: type[hikari.Event], callback: Callable[[Any], Awaitable[None]]) -> None:
        self.handlers.append((event_type, callback))

    async def dispatch(self, event: hikari.Event) -> None:
        for event_type, callback in self.handlers:
            if isinstance(event, event_type):
                await callback(event)


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.bot = FakeBot()
        self.db = Database(tmp_path / "index.sqlite3")
        self.db.open()
        config = Config(
            db_path=tmp_path / "index.sqlite3", guild_id=GUILD_ID, exclude_channel_ids=frozenset({EXCLUDED})
        )
        self.sink = Sink(cast(hikari.GatewayBot, self.bot), self.db, config)
        self.sink.channels = {
            GENERAL: ChannelRecord(GENERAL, "general", ChannelKind.TEXT),
            EXCLUDED: ChannelRecord(EXCLUDED, "admin", ChannelKind.TEXT),
        }
        self.listeners = Listeners(self.sink)
        self.listeners.subscribe(cast(hikari.GatewayBot, self.bot))
        self.listeners.ready = True
        self.member_changes: list[int] = []
        self.thread_changes: list[int] = []
        self.structure_changes = 0
        self.sink.on_member_change(self.member_changes.append)
        self.sink.on_thread_members_change(self.thread_changes.append)
        self.sink.on_structure_change(self._structure)

    def _structure(self) -> None:
        self.structure_changes += 1

    def contents(self) -> dict[int, str]:
        return {int(i): str(c) for i, c in self.db.reader().execute("SELECT id, content FROM messages")}

    async def create(self, mid: int, content: str, channel_id: int = GENERAL, **extra: Any) -> None:
        message = p.message(mid, channel_id, OLA, content, **extra)
        await self.bot.dispatch(hikari.GuildMessageCreateEvent(message=message, shard=SHARD))


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    yield e
    e.db.close()


async def test_events_before_ready_are_ignored(env: Env) -> None:
    env.listeners.ready = False
    await env.create(1, "za wcześnie")
    assert env.contents() == {}
    env.listeners.ready = True
    await env.create(2, "już można")
    assert env.contents() == {2: "już można"}


async def test_other_guilds_unknown_and_excluded_channels_are_ignored(env: Env) -> None:
    await env.create(1, "inny serwer", guild_id="2000")
    await env.create(2, "nieznany kanał", channel_id=99)
    await env.create(3, "wykluczony", channel_id=EXCLUDED)
    assert env.contents() == {}


async def test_message_create_update_and_delete(env: Env) -> None:
    await env.create(1, "pierwsza wersja")
    await env.create(2, "druga")
    await env.create(3, "trzecia")

    edited = p.FACTORY.deserialize_partial_message(
        p.message_payload(1, GENERAL, OLA, "poprawiona wersja", edited_timestamp=p.TIMESTAMP)
    )
    await env.bot.dispatch(hikari.GuildMessageUpdateEvent(old_message=None, message=edited, shard=SHARD))
    assert env.contents()[1] == "poprawiona wersja"
    env.bot.rest.fetch_message.assert_not_called()

    await env.bot.dispatch(
        hikari.GuildMessageDeleteEvent(
            app=APP,
            channel_id=hikari.Snowflake(GENERAL),
            guild_id=hikari.Snowflake(GUILD_ID),
            message_id=hikari.Snowflake(2),
            old_message=None,
            shard=SHARD,
        )
    )
    assert set(env.contents()) == {1, 3}

    await env.bot.dispatch(
        hikari.GuildBulkMessageDeleteEvent(
            app=APP,
            channel_id=hikari.Snowflake(GENERAL),
            guild_id=hikari.Snowflake(GUILD_ID),
            message_ids={hikari.Snowflake(1), hikari.Snowflake(3)},
            old_messages={},
            shard=SHARD,
        )
    )
    assert env.contents() == {}


async def test_partial_update_fetches_the_full_message(env: Env) -> None:
    await env.create(1, "link https://example.com")
    # Discord resolved the link preview: the update carries embeds only, no content and no author.
    partial = p.FACTORY.deserialize_partial_message({
        "id": "1",
        "channel_id": str(GENERAL),
        "guild_id": str(GUILD_ID),
        "embeds": [p.embed("Example", url="https://example.com")],
    })
    assert partial.content is hikari.UNDEFINED
    full = p.message(1, GENERAL, OLA, "link https://example.com (poprawione)")
    env.bot.rest.fetch_message = AsyncMock(return_value=full)

    await env.bot.dispatch(hikari.GuildMessageUpdateEvent(old_message=None, message=partial, shard=SHARD))

    env.bot.rest.fetch_message.assert_awaited_once_with(hikari.Snowflake(GENERAL), hikari.Snowflake(1))
    assert env.contents() == {1: "link https://example.com (poprawione)"}


async def test_thread_create_indexes_the_forum_post_with_tags(env: Env) -> None:
    env.bot.cache.channels[FORUM] = p.channel(p.forum_channel(FORUM, "pomysly", tags={100: "idea"}))
    thread = p.guild_thread(p.thread(20, "Nowe logo", FORUM, owner=1, tags=(100,)))

    await env.bot.dispatch(hikari.GuildThreadCreateEvent(thread=thread, shard=SHARD))

    record = env.sink.channels[20]
    assert (record.kind, record.parent_id, record.tags) == (ChannelKind.FORUM_POST, FORUM, ("idea",))
    row = env.db.reader().execute("SELECT name, kind, tags FROM channels WHERE id = 20").fetchone()
    assert row == ("Nowe logo", "forum_post", "idea")
    assert env.structure_changes == 1

    await env.create(21, "pierwszy post", channel_id=20)
    assert env.contents() == {21: "pierwszy post"}


async def test_member_update_invalidates_only_that_member(env: Env) -> None:
    member = p.member(2, "wikipop", nick="Wiktor", roles=(50,))
    await env.bot.dispatch(hikari.MemberUpdateEvent(old_member=None, member=member, shard=SHARD))

    assert env.member_changes == [2]
    assert env.structure_changes == 0
    row = env.db.reader().execute("SELECT nick, role_ids, in_guild FROM members WHERE id = 2").fetchone()
    assert row == ("Wiktor", "50", 1)

    await env.bot.dispatch(
        hikari.MemberDeleteEvent(
            guild_id=hikari.Snowflake(GUILD_ID),
            user=p.FACTORY.deserialize_user(p.user(2, "wikipop")),
            old_member=None,
            shard=SHARD,
        )
    )
    assert env.member_changes == [2, 2]
    assert env.db.reader().execute("SELECT in_guild FROM members WHERE id = 2").fetchone() == (0,)


async def test_thread_members_update_invalidates_the_thread(env: Env) -> None:
    await env.bot.dispatch(
        hikari.ThreadMembersUpdateEvent(
            app=APP,
            shard=SHARD,
            thread_id=hikari.Snowflake(20),
            guild_id=hikari.Snowflake(GUILD_ID),
            approximate_member_count=2,
            added_members={hikari.Snowflake(2): p.thread_member(20, 2)},
            removed_member_ids=[],
            guild_members={},
            guild_presences={},
        )
    )
    assert env.thread_changes == [20]
    assert env.member_changes == []


async def test_handler_errors_are_logged_not_raised(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    partial = p.FACTORY.deserialize_partial_message({"id": "1", "channel_id": str(GENERAL), "guild_id": str(GUILD_ID)})
    env.bot.rest.fetch_message = AsyncMock(side_effect=p.forbidden())
    await env.bot.dispatch(hikari.GuildMessageUpdateEvent(old_message=None, message=partial, shard=SHARD))
    assert "Handling GuildMessageUpdateEvent failed" in caplog.text
