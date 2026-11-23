"""Which channels a member may read, computed live from their current Discord roles and channel overwrites.

The pure part (``readable_channels``) follows Discord's permission algorithm and is tested on fake roles and
overwrites. ``AccessControl`` builds the input from the hikari cache and caches the result per user briefly.
"""

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import hikari

from zerya_discord_mcp.index.db import Database

logger = logging.getLogger("zerya_discord_mcp.access")

ADMINISTRATOR = 1 << 3
VIEW_CHANNEL = 1 << 10
READ_MESSAGE_HISTORY = 1 << 16
SEND_MESSAGES = 1 << 11
MANAGE_THREADS = 1 << 34
SEND_MESSAGES_IN_THREADS = 1 << 38
ALL = (1 << 53) - 1
READ = VIEW_CHANNEL | READ_MESSAGE_HISTORY


@dataclass(frozen=True, slots=True)
class Overwrite:
    target_id: int
    is_member: bool
    allow: int
    deny: int


@dataclass(frozen=True, slots=True)
class ChannelAcl:
    id: int
    overwrites: tuple[Overwrite, ...] = ()


@dataclass(frozen=True, slots=True)
class ThreadAcl:
    id: int
    parent_id: int
    is_private: bool = False
    owner_id: int | None = None


@dataclass(frozen=True, slots=True)
class GuildAcl:
    guild_id: int
    owner_id: int
    role_permissions: dict[int, int]  # includes @everyone under the guild ID
    channels: dict[int, ChannelAcl]


def base_permissions(guild: GuildAcl, member_id: int, role_ids: Iterable[int]) -> int:
    if member_id == guild.owner_id:
        return ALL
    perms = guild.role_permissions.get(guild.guild_id, 0)
    for role_id in role_ids:
        perms |= guild.role_permissions.get(role_id, 0)
    return ALL if perms & ADMINISTRATOR else perms


def channel_permissions(base: int, guild: GuildAcl, channel: ChannelAcl, member_id: int, role_ids: set[int]) -> int:
    if base & ADMINISTRATOR or base == ALL:
        return ALL
    perms = base
    overwrites = {(o.target_id, o.is_member): o for o in channel.overwrites}
    everyone = overwrites.get((guild.guild_id, False))
    if everyone is not None:
        perms = (perms & ~everyone.deny) | everyone.allow
    allow = deny = 0
    for role_id in role_ids:
        overwrite = overwrites.get((role_id, False))
        if overwrite is not None:
            allow |= overwrite.allow
            deny |= overwrite.deny
    perms = (perms & ~deny) | allow
    member = overwrites.get((member_id, True))
    if member is not None:
        perms = (perms & ~member.deny) | member.allow
    return perms


def readable_channels(
    guild: GuildAcl,
    member_id: int,
    role_ids: Iterable[int],
    threads: Iterable[ThreadAcl],
    private_thread_participants: dict[int, set[int]],
) -> frozenset[int]:
    roles = set(role_ids)
    base = base_permissions(guild, member_id, roles)
    perms: dict[int, int] = {
        cid: channel_permissions(base, guild, channel, member_id, roles) for cid, channel in guild.channels.items()
    }
    readable = {cid for cid, p in perms.items() if p & READ == READ}
    for thread in threads:
        parent = perms.get(thread.parent_id, 0)
        if parent & READ != READ:
            continue
        if thread.is_private and not (
            parent & MANAGE_THREADS
            or thread.owner_id == member_id
            or member_id in private_thread_participants.get(thread.id, set())
        ):
            continue
        readable.add(thread.id)
    return frozenset(readable)


def can_send(
    guild: GuildAcl, member_id: int, role_ids: Iterable[int], channel_id: int, thread_parent_id: int | None
) -> bool:
    """Whether the member may post in a channel (or, given its parent, in a thread). Readability of private
    threads is checked separately by ``readable_channels``."""
    roles = set(role_ids)
    base = base_permissions(guild, member_id, roles)
    target = thread_parent_id if thread_parent_id is not None else channel_id
    channel = guild.channels.get(target)
    if channel is None:
        return False
    perms = channel_permissions(base, guild, channel, member_id, roles)
    needed = READ | (SEND_MESSAGES_IN_THREADS if thread_parent_id is not None else SEND_MESSAGES)
    return perms & needed == needed


def _overwrites(channel: hikari.PermissibleGuildChannel) -> tuple[Overwrite, ...]:
    return tuple(
        Overwrite(
            target_id=int(o.id),
            is_member=o.type == hikari.PermissionOverwriteType.MEMBER,
            allow=int(o.allow),
            deny=int(o.deny),
        )
        for o in channel.permission_overwrites.values()
    )


THREAD_MEMBERS_TTL = 600.0


class AccessControl:
    """Allowed channel IDs per user, cached for ``ttl`` seconds.

    Role and channel changes drop the whole cache, member changes only that member's entry. Private thread
    membership comes from Discord (thread member lists, cached per thread and dropped on membership events).
    """

    def __init__(
        self,
        bot: hikari.GatewayBot,
        db: Database,
        guild_id: int,
        ttl: float,
        is_indexed: Callable[[int], bool],
    ) -> None:
        self.bot = bot
        self.db = db
        self.guild_id = guild_id
        self.ttl = ttl
        self.is_indexed = is_indexed
        self._cache: dict[int, tuple[float, frozenset[int]]] = {}
        self._thread_members: dict[int, tuple[float, frozenset[int]]] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    def invalidate_user(self, user_id: int) -> None:
        self._cache.pop(user_id, None)

    def invalidate_thread(self, thread_id: int) -> None:
        self._thread_members.pop(thread_id, None)
        self._cache.clear()

    def _guild_acl(self) -> GuildAcl:
        cache = self.bot.cache
        guild = cache.get_guild(self.guild_id)
        if guild is None:
            raise RuntimeError("Guild is not in the bot cache yet")
        roles = cache.get_roles_view_for_guild(self.guild_id)
        channels = cache.get_guild_channels_view_for_guild(self.guild_id)
        return GuildAcl(
            guild_id=self.guild_id,
            owner_id=int(guild.owner_id),
            role_permissions={int(rid): int(r.permissions) for rid, r in roles.items()},
            channels={
                int(cid): ChannelAcl(id=int(cid), overwrites=_overwrites(c))
                for cid, c in channels.items()
                if isinstance(c, hikari.PermissibleGuildChannel)
            },
        )

    @staticmethod
    def _threads(conn: sqlite3.Connection) -> list[ThreadAcl]:
        return [
            ThreadAcl(id=int(i), parent_id=int(p), is_private=bool(priv), owner_id=int(o) if o is not None else None)
            for i, p, priv, o in conn.execute(
                "SELECT id, parent_id, is_private, owner_id FROM channels "
                "WHERE kind IN ('thread', 'forum_post') AND parent_id IS NOT NULL"
            )
        ]

    async def _members_of(self, thread_id: int) -> frozenset[int]:
        cached = self._thread_members.get(thread_id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]
        try:
            members = frozenset([int(m.user_id) async for m in self.bot.rest.fetch_thread_members(thread_id)])
        except hikari.ForbiddenError, hikari.NotFoundError:
            members = frozenset()
        self._thread_members[thread_id] = (time.monotonic() + THREAD_MEMBERS_TTL, members)
        return members

    async def _member(self, user_id: int) -> hikari.Member | None:
        member = self.bot.cache.get_member(self.guild_id, user_id)
        if member is not None:
            return member
        try:
            return await self.bot.rest.fetch_member(self.guild_id, user_id)
        except hikari.NotFoundError:
            return None

    async def can_send(self, user_id: int, channel_id: int, thread_parent_id: int | None) -> bool:
        member = await self._member(user_id)
        if member is None:
            return False
        if member.communication_disabled_until() is not None:
            return False  # timed out
        return can_send(self._guild_acl(), user_id, [int(r) for r in member.role_ids], channel_id, thread_parent_id)

    async def allowed_channels(self, user_id: int) -> frozenset[int] | None:
        """None when the user is not on the server (any connection of theirs is then useless)."""
        cached = self._cache.get(user_id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]
        member = await self._member(user_id)
        if member is None:
            self._cache.pop(user_id, None)
            return None
        guild = self._guild_acl()
        roles = [int(r) for r in member.role_ids]
        threads = await asyncio.to_thread(lambda: self._threads(self.db.reader()))
        readable = readable_channels(guild, user_id, roles, threads, {})
        # Private threads the user could only see as a member: ask Discord who the members are.
        pending = [t for t in threads if t.is_private and t.parent_id in readable and t.id not in readable]
        members = {t.id: await self._members_of(t.id) for t in pending}
        if any(user_id in m for m in members.values()):
            readable = readable_channels(guild, user_id, roles, threads, {k: set(v) for k, v in members.items()})
        allowed = frozenset(cid for cid in readable if self.is_indexed(cid))
        self._cache[user_id] = (time.monotonic() + self.ttl, allowed)
        return allowed
