"""History backfill: structure (roles, members, channels, threads) and messages from per-channel cursors.

Each channel first catches up from its newest indexed message to now, then continues backwards from its oldest
indexed message until the start of the channel (or ``backfill_since``). Restarts resume from the cursors.
Listeners never move cursors, so the catch-up pass covers whatever happened while the bot was offline.
"""

import asyncio
import logging
import sqlite3
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import hikari

from zerya_discord_mcp.index import writer
from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord, snowflake_at
from zerya_discord_mcp.ingest.normalize import channel_record, member_record, role_record
from zerya_discord_mcp.ingest.sink import Sink

logger = logging.getLogger("zerya_discord_mcp.backfill")
BATCH = 100


async def _batches(iterator: hikari.LazyIterator[hikari.Message]) -> AsyncIterator[list[hikari.Message]]:
    batch: list[hikari.Message] = []
    async for message in iterator:
        batch.append(message)
        if len(batch) >= BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


class Backfill:
    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.bot = sink.bot
        self.guild_id = sink.config.guild_id
        self._last_message: dict[int, int] = {}
        self.indexed_messages = 0
        self.failed_channels = 0
        self.done = asyncio.Event()

    # Structure ------------------------------------------------------------------------------------------

    async def sync_structure(self) -> list[ChannelRecord]:
        rest = self.bot.rest
        roles = await rest.fetch_roles(self.guild_id)
        members = [member_record(m) async for m in rest.fetch_members(self.guild_id)]

        channels: dict[int, hikari.GuildChannel] = {
            int(c.id): c for c in await rest.fetch_guild_channels(self.guild_id)
        }
        complete_listing = True
        threads: dict[int, hikari.GuildThreadChannel] = {
            int(t.id): t for t in await rest.fetch_active_threads(self.guild_id)
        }
        for channel in list(channels.values()):
            if not isinstance(channel, hikari.GuildTextChannel | hikari.GuildNewsChannel | hikari.GuildForumChannel):
                continue
            if not self.sink.is_indexed(int(channel.id)):
                continue
            try:
                async for thread in rest.fetch_public_archived_threads(channel):
                    threads[int(thread.id)] = thread
                if not isinstance(channel, hikari.GuildForumChannel):
                    async for private in rest.fetch_private_archived_threads(channel):
                        threads[int(private.id)] = private
            except (hikari.ForbiddenError, hikari.NotFoundError) as e:
                complete_listing = False
                logger.warning("Cannot list archived threads of #%s: %s", channel.name, e)

        def parent(cid: int) -> hikari.GuildChannel | None:
            return channels.get(cid)

        records = [channel_record(c, parent) for c in channels.values()]
        records += [channel_record(t, parent) for t in threads.values()]
        for item in [*channels.values(), *threads.values()]:
            last = getattr(item, "last_message_id", None)
            self._last_message[int(item.id)] = int(last) if last is not None else 0

        known = {r.id for r in records}

        def apply(conn: sqlite3.Connection) -> None:
            writer.replace_roles(conn, [role_record(r) for r in roles if r.id != self.guild_id])
            conn.execute("UPDATE members SET in_guild = 0")
            writer.upsert_members(conn, members)
            writer.upsert_channels(conn, records)
            if complete_listing:
                gone = [int(r[0]) for r in conn.execute("SELECT id FROM channels") if int(r[0]) not in known]
                for channel_id in gone:
                    writer.delete_channel(conn, channel_id)

        await asyncio.to_thread(self.sink.db.write, apply)
        self.sink.channels = {r.id: r for r in records}
        self.sink.structure_changed()
        logger.info(
            "Structure synced: %d roles, %d members, %d channels and threads", len(roles), len(members), len(records)
        )
        return records

    # Messages -------------------------------------------------------------------------------------------

    def _cursor(self, channel_id: int) -> tuple[int | None, int | None, bool]:
        row = (
            self.sink.db
            .reader()
            .execute("SELECT newest_id, oldest_id, complete FROM cursors WHERE channel_id = ?", (channel_id,))
            .fetchone()
        )
        if row is None:
            return None, None, False
        return row[0], row[1], bool(row[2])

    def _since_id(self) -> int:
        since = self.sink.config.backfill_since
        if since is None:
            return 0
        tz = ZoneInfo(self.sink.config.timezone)
        return snowflake_at(datetime(since.year, since.month, since.day, tzinfo=tz))

    async def _save_cursor(
        self,
        channel_id: int,
        *,
        newest_id: int | None = None,
        oldest_id: int | None = None,
        complete: bool | None = None,
    ) -> None:
        await asyncio.to_thread(
            self.sink.db.write,
            lambda conn: writer.save_cursor(
                conn, channel_id, newest_id=newest_id, oldest_id=oldest_id, complete=complete
            ),
        )

    async def backfill_channel(self, record: ChannelRecord) -> int:
        channel_id = record.id
        newest, oldest, complete = self._cursor(channel_id)
        since_id = self._since_id()
        count = 0
        rest = self.bot.rest

        # A channel that was empty when its history was completed has no newest_id: catch up from the cut-off.
        caught_up_to = newest if newest is not None else (since_id if complete else None)
        if caught_up_to is not None and self._last_message.get(channel_id, 1) > caught_up_to:
            async for batch in _batches(rest.fetch_messages(channel_id, after=caught_up_to)):
                count += await self.sink.write_messages(batch)
                await self._save_cursor(channel_id, newest_id=max(int(m.id) for m in batch))

        if not complete:
            start: hikari.SearchableSnowflakeish = (
                oldest if oldest is not None else hikari.Snowflake.from_datetime(datetime.now().astimezone())
            )
            reached_limit = False
            async for batch in _batches(rest.fetch_messages(channel_id, before=start)):
                kept = [m for m in batch if int(m.id) >= since_id]
                reached_limit = len(kept) < len(batch)
                if kept:
                    count += await self.sink.write_messages(kept)
                ids = [int(m.id) for m in batch]
                await self._save_cursor(channel_id, newest_id=max(ids), oldest_id=min(ids))
                if reached_limit:
                    break
            await self._save_cursor(channel_id, complete=True)
        return count

    # Reconciliation ---------------------------------------------------------------------------------------

    async def reconcile_channel(self, channel_id: int, since_id: int, until_id: int) -> tuple[int, int]:
        """Re-reads a channel after ``since_id``: applies edits made while offline and forgets deleted messages.

        Only messages up to ``until_id`` (the start of the run) are considered for deletion, so messages the
        listeners store while this runs are never removed.
        """
        seen: set[int] = set()
        updated = 0
        async for batch in _batches(self.bot.rest.fetch_messages(channel_id, after=since_id)):
            updated += await self.sink.write_messages(batch)
            seen.update(int(m.id) for m in batch)

        def apply(conn: sqlite3.Connection) -> int:
            stored = conn.execute(
                "SELECT id FROM messages WHERE channel_id = ? AND id > ? AND id <= ?", (channel_id, since_id, until_id)
            )
            gone = [int(r[0]) for r in stored if int(r[0]) not in seen]
            writer.delete_messages(conn, gone)
            return len(gone)

        deleted = await asyncio.to_thread(self.sink.db.write, apply)
        return updated, deleted

    async def reconcile(self, days: int) -> tuple[int, int]:
        """Reconciles every indexed channel with activity in the last ``days`` days."""
        now = datetime.now().astimezone()
        since_id = snowflake_at(now - timedelta(days=days))
        until_id = snowflake_at(now)
        recent = {
            int(r[0])
            for r in self.sink.db.reader().execute("SELECT DISTINCT channel_id FROM messages WHERE id > ?", (since_id,))
        }
        recent |= {cid for cid, last in self._last_message.items() if last > since_id}
        updated = deleted = 0
        for channel_id in sorted(recent):
            record = self.sink.channels.get(channel_id)
            if record is None or not self.sink.indexable(record):
                continue
            try:
                u, d = await self.reconcile_channel(channel_id, since_id, until_id)
            except hikari.ForbiddenError, hikari.NotFoundError:
                continue
            updated += u
            deleted += d
        logger.info(
            "Reconciled %d channels (last %d days): %d messages re-read, %d deleted",
            len(recent),
            days,
            updated,
            deleted,
        )
        return updated, deleted

    async def run(self) -> None:
        started = time.monotonic()
        try:
            records = await self.sync_structure()
            targets = [r for r in records if self.sink.indexable(r)]
            targets.sort(key=lambda r: -self._last_message.get(r.id, 0))
            semaphore = asyncio.Semaphore(self.sink.config.backfill_concurrency)
            done = 0

            async def one(record: ChannelRecord) -> None:
                nonlocal done
                async with semaphore:
                    try:
                        n = await self.backfill_channel(record)
                    except (hikari.ForbiddenError, hikari.NotFoundError) as e:
                        logger.info("Skipping %s (%s): %s", record.name, record.kind.value, e)
                        return
                    except Exception:
                        self.failed_channels += 1
                        logger.exception("Backfill of %s failed", record.name)
                        return
                    self.indexed_messages += n
                    done += 1
                    if n:
                        label = (
                            f"{record.name} [{record.kind.value}]"
                            if record.kind is not ChannelKind.TEXT
                            else record.name
                        )
                        logger.info("Backfilled %s: %d messages (%d/%d channels)", label, n, done, len(targets))

            await asyncio.gather(*(one(r) for r in targets))
            logger.info(
                "Backfill finished: %d messages in %.0fs across %d channels",
                self.indexed_messages,
                time.monotonic() - started,
                len(targets),
            )
        finally:
            self.done.set()
