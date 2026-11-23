from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import hikari
import pytest
from hikari.iterators import FlatLazyIterator

from zerya_discord_mcp.access.permissions import (
    ADMINISTRATOR,
    MANAGE_THREADS,
    READ,
    SEND_MESSAGES,
    SEND_MESSAGES_IN_THREADS,
    VIEW_CHANNEL,
    AccessControl,
    ChannelAcl,
    GuildAcl,
    Overwrite,
    ThreadAcl,
    can_send,
    readable_channels,
)
from zerya_discord_mcp.index.models import ChannelKind
from zerya_discord_mcp.testing.fixtures import IndexBuilder
from zerya_discord_mcp.tests import payloads

GUILD = 1
OWNER = 99
MEMBER_ROLE = 10
ADMIN_ROLE = 11
MOD_ROLE = 12
PUBLIC, PRIVATE, MEMBERS_ONLY, DENIED_FOR_USER = 100, 101, 102, 103


def _guild() -> GuildAcl:
    return GuildAcl(
        guild_id=GUILD,
        owner_id=OWNER,
        role_permissions={GUILD: READ, MEMBER_ROLE: 0, ADMIN_ROLE: ADMINISTRATOR, MOD_ROLE: MANAGE_THREADS},
        channels={
            PUBLIC: ChannelAcl(PUBLIC),
            PRIVATE: ChannelAcl(PRIVATE, (Overwrite(GUILD, False, 0, VIEW_CHANNEL),)),
            MEMBERS_ONLY: ChannelAcl(
                MEMBERS_ONLY,
                (Overwrite(GUILD, False, 0, VIEW_CHANNEL), Overwrite(MEMBER_ROLE, False, VIEW_CHANNEL, 0)),
            ),
            DENIED_FOR_USER: ChannelAcl(DENIED_FOR_USER, (Overwrite(5, True, 0, VIEW_CHANNEL),)),
        },
    )


def _readable(user: int, roles: list[int], threads: list[ThreadAcl] | None = None) -> frozenset[int]:
    return readable_channels(_guild(), user, roles, threads or [], {200: {7}})


def test_everyone_and_role_overwrites() -> None:
    assert _readable(5, []) == {PUBLIC}
    assert _readable(6, [MEMBER_ROLE]) == {PUBLIC, MEMBERS_ONLY, DENIED_FOR_USER}


def test_member_overwrite_beats_roles() -> None:
    assert DENIED_FOR_USER not in _readable(5, [MEMBER_ROLE])


def test_admin_and_owner_see_everything() -> None:
    everything = {PUBLIC, PRIVATE, MEMBERS_ONLY, DENIED_FOR_USER}
    assert _readable(5, [ADMIN_ROLE]) == everything
    assert _readable(OWNER, []) == everything


def test_threads_follow_parent_and_private_threads_need_membership() -> None:
    threads = [ThreadAcl(200, PUBLIC, is_private=True, owner_id=8), ThreadAcl(201, PRIVATE), ThreadAcl(202, PUBLIC)]
    assert _readable(5, [], threads) == {PUBLIC, 202}
    assert 200 in _readable(7, [], threads)  # participant
    assert 200 in _readable(8, [], threads)  # owner
    assert 200 in _readable(5, [MOD_ROLE], threads)  # MANAGE_THREADS


# Posting ----------------------------------------------------------------------------------------------------

TALKER_ROLE = 13
THREADER_ROLE = 14
ANNOUNCEMENTS, NO_THREADS = 104, 105


def _send_guild() -> GuildAcl:
    guild = _guild()
    return GuildAcl(
        guild_id=GUILD,
        owner_id=OWNER,
        role_permissions={
            GUILD: READ | SEND_MESSAGES,
            THREADER_ROLE: SEND_MESSAGES_IN_THREADS,
            ADMIN_ROLE: ADMINISTRATOR,
            TALKER_ROLE: 0,
        },
        channels={
            **guild.channels,
            ANNOUNCEMENTS: ChannelAcl(
                ANNOUNCEMENTS,
                (Overwrite(GUILD, False, 0, SEND_MESSAGES), Overwrite(TALKER_ROLE, False, SEND_MESSAGES, 0)),
            ),
            NO_THREADS: ChannelAcl(NO_THREADS, (Overwrite(THREADER_ROLE, False, 0, SEND_MESSAGES_IN_THREADS),)),
        },
    )


@pytest.mark.parametrize(
    ("user", "roles", "channel", "parent", "expected"),
    [
        (5, [], PUBLIC, None, True),
        (5, [], PRIVATE, None, False),  # cannot even see it
        (5, [], ANNOUNCEMENTS, None, False),
        (5, [TALKER_ROLE], ANNOUNCEMENTS, None, True),  # role overwrite beats @everyone
        (5, [], 999, None, False),  # unknown channel
        # Threads need SEND_MESSAGES_IN_THREADS on the parent; SEND_MESSAGES is not enough.
        (5, [], 300, PUBLIC, False),
        (5, [THREADER_ROLE], 300, PUBLIC, True),
        (5, [THREADER_ROLE], 300, NO_THREADS, False),
        (5, [THREADER_ROLE], 300, PRIVATE, False),
        (5, [ADMIN_ROLE], 300, PRIVATE, True),
        (OWNER, [], ANNOUNCEMENTS, None, True),
    ],
)
def test_can_send_matrix(user: int, roles: list[int], channel: int, parent: int | None, expected: bool) -> None:
    assert can_send(_send_guild(), user, roles, channel, parent) is expected


def test_member_overwrite_can_take_posting_away() -> None:
    guild = _send_guild()
    muted = GuildAcl(
        guild_id=GUILD,
        owner_id=OWNER,
        role_permissions=guild.role_permissions,
        channels={PUBLIC: ChannelAcl(PUBLIC, (Overwrite(5, True, 0, SEND_MESSAGES),))},
    )
    assert not can_send(muted, 5, [TALKER_ROLE], PUBLIC, None)
    assert can_send(muted, 6, [], PUBLIC, None)


# AccessControl over the bot cache --------------------------------------------------------------------------

THREAD_OWNER, THREAD_MEMBER, OUTSIDER, MODERATOR, EXCLUDED_CHANNEL = 8, 7, 5, 6, 106
PRIVATE_THREAD, PUBLIC_THREAD = 200, 201


class FakeCache:
    def __init__(self) -> None:
        self.guild = SimpleNamespace(owner_id=hikari.Snowflake(OWNER))
        self.roles = {
            hikari.Snowflake(GUILD): payloads.role(GUILD, "@everyone", permissions=READ | SEND_MESSAGES),
            hikari.Snowflake(MOD_ROLE): payloads.role(MOD_ROLE, "Mod", permissions=MANAGE_THREADS),
            hikari.Snowflake(THREADER_ROLE): payloads.role(THREADER_ROLE, "T", permissions=SEND_MESSAGES_IN_THREADS),
        }
        self.channels = {
            hikari.Snowflake(c.id): c
            for c in (
                payloads.channel(payloads.text_channel(PUBLIC, "general")),
                payloads.channel(
                    payloads.text_channel(PRIVATE, "admin", overwrites=[payloads.overwrite(GUILD, deny=VIEW_CHANNEL)])
                ),
                payloads.channel(payloads.text_channel(EXCLUDED_CHANNEL, "excluded")),
            )
        }
        self.members: dict[int, hikari.Member] = {
            uid: payloads.member(uid, f"user{uid}") for uid in (THREAD_OWNER, THREAD_MEMBER, OUTSIDER)
        }
        self.members[MODERATOR] = payloads.member(MODERATOR, "mod", roles=(MOD_ROLE,))

    def get_guild(self, guild_id: int) -> SimpleNamespace | None:
        return self.guild if guild_id == GUILD else None

    def get_roles_view_for_guild(self, guild_id: int) -> dict[hikari.Snowflake, hikari.Role]:
        return self.roles

    def get_guild_channels_view_for_guild(self, guild_id: int) -> dict[hikari.Snowflake, hikari.GuildChannel]:
        return self.channels

    def get_member(self, guild_id: int, user_id: int) -> hikari.Member | None:
        return self.members.get(user_id)


class FakeRest:
    def __init__(self) -> None:
        self.thread_members: dict[int, list[int]] = {PRIVATE_THREAD: [THREAD_OWNER, THREAD_MEMBER]}
        self.thread_member_calls: list[int] = []
        self.remote_members: dict[int, hikari.Member] = {}

    def fetch_thread_members(self, thread_id: int) -> FlatLazyIterator[hikari.ThreadMember]:
        self.thread_member_calls.append(thread_id)
        users = self.thread_members.get(thread_id, [])
        return FlatLazyIterator([payloads.thread_member(thread_id, u) for u in users])

    async def fetch_member(self, guild_id: int, user_id: int) -> hikari.Member:
        if user_id not in self.remote_members:
            raise hikari.NotFoundError(url="https://discord.com/api", headers={}, raw_body="")
        return self.remote_members[user_id]


class FakeBot:
    def __init__(self) -> None:
        self.cache = FakeCache()
        self.rest = FakeRest()


@pytest.fixture
def access(tmp_path: Path) -> Iterator[tuple[AccessControl, FakeBot]]:
    b = IndexBuilder(tmp_path / "index.sqlite3")
    b.channel("general", channel_id=PUBLIC)
    b.channel("admin", channel_id=PRIVATE)
    b.channel("excluded", channel_id=EXCLUDED_CHANNEL)
    b.channel("tajny", ChannelKind.THREAD, PUBLIC, channel_id=PRIVATE_THREAD, is_private=True, owner_id=THREAD_OWNER)
    b.channel("jawny", ChannelKind.THREAD, PUBLIC, channel_id=PUBLIC_THREAD)
    bot = FakeBot()
    control = AccessControl(
        cast(hikari.GatewayBot, bot), b.db, GUILD, ttl=60.0, is_indexed=lambda cid: cid != EXCLUDED_CHANNEL
    )
    yield control, bot
    b.db.close()


async def test_private_thread_is_visible_only_to_its_members(access: tuple[AccessControl, FakeBot]) -> None:
    control, bot = access
    assert await control.allowed_channels(OUTSIDER) == {PUBLIC, PUBLIC_THREAD}
    assert await control.allowed_channels(THREAD_MEMBER) == {PUBLIC, PUBLIC_THREAD, PRIVATE_THREAD}
    assert bot.rest.thread_member_calls == [PRIVATE_THREAD]  # member list cached across users


async def test_thread_owner_and_moderators_see_private_threads_without_asking(
    access: tuple[AccessControl, FakeBot],
) -> None:
    control, bot = access
    assert PRIVATE_THREAD in (await control.allowed_channels(THREAD_OWNER) or set())
    moderator = await control.allowed_channels(MODERATOR)
    assert moderator == {PUBLIC, PUBLIC_THREAD, PRIVATE_THREAD}
    assert bot.rest.thread_member_calls == []
    assert OWNER not in bot.cache.members
    bot.rest.remote_members[OWNER] = payloads.member(OWNER, "owner")  # not cached: fetched over REST
    assert await control.allowed_channels(OWNER) == {PUBLIC, PRIVATE, PUBLIC_THREAD, PRIVATE_THREAD}


async def test_people_off_the_server_get_nothing(access: tuple[AccessControl, FakeBot]) -> None:
    control, _ = access
    assert await control.allowed_channels(12345) is None
    assert not await control.can_send(12345, PUBLIC, None)


async def test_timed_out_member_cannot_send(access: tuple[AccessControl, FakeBot]) -> None:
    control, bot = access
    assert await control.can_send(OUTSIDER, PUBLIC, None)
    assert not await control.can_send(OUTSIDER, PUBLIC_THREAD, PUBLIC)  # no SEND_MESSAGES_IN_THREADS
    until = datetime.now(UTC) + timedelta(hours=1)
    bot.cache.members[OUTSIDER] = payloads.member(OUTSIDER, "user5", timed_out_until=until)
    assert not await control.can_send(OUTSIDER, PUBLIC, None)
    expired = datetime.now(UTC) - timedelta(hours=1)
    bot.cache.members[OUTSIDER] = payloads.member(OUTSIDER, "user5", roles=(THREADER_ROLE,), timed_out_until=expired)
    assert await control.can_send(OUTSIDER, PUBLIC_THREAD, PUBLIC)


async def test_invalidate_user_drops_only_that_user(access: tuple[AccessControl, FakeBot]) -> None:
    control, bot = access
    assert await control.allowed_channels(OUTSIDER) == {PUBLIC, PUBLIC_THREAD}
    assert await control.allowed_channels(THREAD_MEMBER) == {PUBLIC, PUBLIC_THREAD, PRIVATE_THREAD}
    # Both become moderators; cached answers stay until invalidated.
    for uid in (OUTSIDER, THREAD_MEMBER):
        bot.cache.members[uid] = payloads.member(uid, f"user{uid}", roles=(MOD_ROLE,))
    bot.cache.channels.pop(hikari.Snowflake(PUBLIC))  # and general goes away for everyone
    assert await control.allowed_channels(OUTSIDER) == {PUBLIC, PUBLIC_THREAD}

    control.invalidate_user(OUTSIDER)
    assert await control.allowed_channels(OUTSIDER) == frozenset()
    assert await control.allowed_channels(THREAD_MEMBER) == {PUBLIC, PUBLIC_THREAD, PRIVATE_THREAD}

    control.invalidate()
    assert await control.allowed_channels(THREAD_MEMBER) == frozenset()


async def test_invalidate_thread_refetches_its_members(access: tuple[AccessControl, FakeBot]) -> None:
    control, bot = access
    assert PRIVATE_THREAD not in (await control.allowed_channels(OUTSIDER) or set())
    bot.rest.thread_members[PRIVATE_THREAD].append(OUTSIDER)
    assert PRIVATE_THREAD not in (await control.allowed_channels(OUTSIDER) or set())  # still cached

    control.invalidate_thread(PRIVATE_THREAD)
    assert PRIVATE_THREAD in (await control.allowed_channels(OUTSIDER) or set())
    assert bot.rest.thread_member_calls == [PRIVATE_THREAD, PRIVATE_THREAD]
