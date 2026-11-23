"""Shared plumbing for backfill and listeners: what to index, name resolution and writes off the event loop."""

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Iterable, Sequence

import hikari

from zerya_discord_mcp.config import Config
from zerya_discord_mcp.index import writer
from zerya_discord_mcp.index.db import Database
from zerya_discord_mcp.index.models import ChannelRecord, MemberRecord, MessageRecord
from zerya_discord_mcp.ingest.normalize import Resolvers, author_record, is_message_channel, message_record

logger = logging.getLogger("zerya_discord_mcp.ingest")


class Sink:
    def __init__(self, bot: hikari.GatewayBot, db: Database, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self.channels: dict[int, ChannelRecord] = {}
        self._structure_listeners: list[Callable[[], None]] = []
        self._member_listeners: list[Callable[[int], None]] = []
        self._thread_member_listeners: list[Callable[[int], None]] = []
        self.resolvers = Resolvers(user=self._user_name, role=self._role_name)

    def on_structure_change(self, callback: Callable[[], None]) -> None:
        self._structure_listeners.append(callback)

    def on_member_change(self, callback: Callable[[int], None]) -> None:
        self._member_listeners.append(callback)

    def on_thread_members_change(self, callback: Callable[[int], None]) -> None:
        self._thread_member_listeners.append(callback)

    def member_changed(self, user_id: int) -> None:
        for callback in self._member_listeners:
            callback(user_id)

    def thread_members_changed(self, thread_id: int) -> None:
        for callback in self._thread_member_listeners:
            callback(thread_id)

    def structure_changed(self) -> None:
        for callback in self._structure_listeners:
            callback()

    # Scope ----------------------------------------------------------------------------------------------

    def lineage(self, channel_id: int) -> list[int]:
        """The channel, its parent (for threads) and its category."""
        out: list[int] = []
        current: int | None = channel_id
        while current is not None and len(out) < 4:
            out.append(current)
            record = self.channels.get(current)
            current = record.parent_id if record else None
        return out

    def is_indexed(self, channel_id: int) -> bool:
        """Channel (or the thread's parent) passes the configured allowlist and blocklist."""
        lineage = self.lineage(channel_id)
        if any(cid in self.config.exclude_channel_ids for cid in lineage):
            return False
        if self.config.index_channel_ids:
            return any(cid in self.config.index_channel_ids for cid in lineage)
        return True

    def indexable(self, record: ChannelRecord) -> bool:
        return is_message_channel(record) and self.is_indexed(record.id)

    # Names ----------------------------------------------------------------------------------------------

    def _user_name(self, user_id: int) -> str | None:
        member = self.bot.cache.get_member(self.config.guild_id, user_id)
        if member is not None:
            return member.display_name
        user = self.bot.cache.get_user(user_id)
        return (user.global_name or user.username) if user is not None else None

    def _role_name(self, role_id: int) -> str | None:
        role = self.bot.cache.get_role(role_id)
        return role.name if role is not None else None

    # Writes ---------------------------------------------------------------------------------------------

    def records(self, messages: Iterable[hikari.PartialMessage]) -> tuple[list[MessageRecord], list[MemberRecord]]:
        records: list[MessageRecord] = []
        authors: dict[int, MemberRecord] = {}
        for message in messages:
            record = message_record(message, self.resolvers)
            if record is not None:
                records.append(record)
            author = author_record(message)
            if author is not None:
                authors[author.id] = author
        return records, list(authors.values())

    async def write_messages(self, messages: Sequence[hikari.PartialMessage]) -> int:
        records, authors = self.records(messages)

        def apply(conn: sqlite3.Connection) -> None:
            writer.upsert_authors(conn, authors)
            writer.upsert_messages(conn, records)

        await asyncio.to_thread(self.db.write, apply)
        return len(records)

    async def delete_messages(self, message_ids: Iterable[int]) -> None:
        ids = list(message_ids)
        await asyncio.to_thread(self.db.write, lambda conn: writer.delete_messages(conn, ids))

    async def upsert_channels(self, records: Sequence[ChannelRecord]) -> None:
        for record in records:
            self.channels[record.id] = record
        await asyncio.to_thread(self.db.write, lambda conn: writer.upsert_channels(conn, records))
        self.structure_changed()

    async def delete_channel(self, channel_id: int) -> None:
        self.channels.pop(channel_id, None)
        await asyncio.to_thread(self.db.write, lambda conn: writer.delete_channel(conn, channel_id))
        self.structure_changed()
