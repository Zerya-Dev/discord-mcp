"""Live updates from the gateway: new, edited and deleted messages plus structure changes."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import hikari

from zerya_discord_mcp.index import writer
from zerya_discord_mcp.ingest.normalize import channel_record, member_record, role_record
from zerya_discord_mcp.ingest.sink import Sink

logger = logging.getLogger("zerya_discord_mcp.listeners")


class Listeners:
    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.ready = False  # set once migrations ran; earlier events are covered by the backfill catch-up

    def subscribe(self, bot: hikari.GatewayBot) -> None:
        handlers: list[tuple[type[hikari.Event], Callable[..., Awaitable[None]]]] = [
            (hikari.GuildMessageCreateEvent, self.on_message_create),
            (hikari.GuildMessageUpdateEvent, self.on_message_update),
            (hikari.GuildMessageDeleteEvent, self.on_message_delete),
            (hikari.GuildBulkMessageDeleteEvent, self.on_bulk_delete),
            (hikari.GuildChannelCreateEvent, self.on_channel),
            (hikari.GuildChannelUpdateEvent, self.on_channel),
            (hikari.GuildChannelDeleteEvent, self.on_channel_delete),
            (hikari.GuildThreadCreateEvent, self.on_thread),
            (hikari.GuildThreadUpdateEvent, self.on_thread),
            (hikari.GuildThreadDeleteEvent, self.on_thread_delete),
            (hikari.MemberCreateEvent, self.on_member),
            (hikari.MemberUpdateEvent, self.on_member),
            (hikari.MemberDeleteEvent, self.on_member_delete),
            (hikari.ThreadMembersUpdateEvent, self.on_thread_members),
            (hikari.RoleCreateEvent, self.on_role),
            (hikari.RoleUpdateEvent, self.on_role),
            (hikari.RoleDeleteEvent, self.on_role),
        ]
        for event_type, handler in handlers:
            bot.subscribe(event_type, self._guarded(handler))

    def _guarded[E: hikari.Event](self, handler: Callable[[E], Awaitable[None]]) -> Callable[[E], Awaitable[None]]:
        async def run(event: E) -> None:
            if not self.ready:
                return
            guild_id = getattr(event, "guild_id", None)
            if guild_id is not None and int(guild_id) != self.sink.config.guild_id:
                return
            try:
                await handler(event)
            except Exception:
                logger.exception("Handling %s failed", type(event).__name__)

        return run

    def _parent(self, channel_id: int) -> hikari.GuildChannel | None:
        return self.sink.bot.cache.get_guild_channel(channel_id)

    # Messages -------------------------------------------------------------------------------------------

    async def on_message_create(self, event: hikari.GuildMessageCreateEvent) -> None:
        if self.sink.is_indexed(int(event.channel_id)) and int(event.channel_id) in self.sink.channels:
            await self.sink.write_messages([event.message])

    async def on_message_update(self, event: hikari.GuildMessageUpdateEvent) -> None:
        if not self.sink.is_indexed(int(event.channel_id)) or int(event.channel_id) not in self.sink.channels:
            return
        message = event.message
        if message.content is hikari.UNDEFINED or message.author is hikari.UNDEFINED:
            # Partial update (e.g. embed resolved): fetch the full message instead of guessing.
            message = await self.sink.bot.rest.fetch_message(event.channel_id, event.message_id)
        await self.sink.write_messages([message])

    async def on_message_delete(self, event: hikari.GuildMessageDeleteEvent) -> None:
        await self.sink.delete_messages([int(event.message_id)])

    async def on_bulk_delete(self, event: hikari.GuildBulkMessageDeleteEvent) -> None:
        await self.sink.delete_messages(int(mid) for mid in event.message_ids)

    # Structure ------------------------------------------------------------------------------------------

    async def on_channel(self, event: hikari.GuildChannelCreateEvent | hikari.GuildChannelUpdateEvent) -> None:
        await self.sink.upsert_channels([channel_record(event.channel, self._parent)])

    async def on_channel_delete(self, event: hikari.GuildChannelDeleteEvent) -> None:
        await self.sink.delete_channel(int(event.channel_id))

    async def on_thread(self, event: hikari.GuildThreadCreateEvent | hikari.GuildThreadUpdateEvent) -> None:
        await self.sink.upsert_channels([channel_record(event.thread, self._parent)])

    async def on_thread_delete(self, event: hikari.GuildThreadDeleteEvent) -> None:
        await self.sink.delete_channel(int(event.thread_id))

    async def on_member(self, event: hikari.MemberCreateEvent | hikari.MemberUpdateEvent) -> None:
        record = member_record(event.member)
        await asyncio.to_thread(self.sink.db.write, lambda conn: writer.upsert_members(conn, [record]))
        self.sink.member_changed(record.id)

    async def on_member_delete(self, event: hikari.MemberDeleteEvent) -> None:
        user_id = int(event.user_id)
        await asyncio.to_thread(self.sink.db.write, lambda conn: writer.mark_member_left(conn, user_id))
        self.sink.member_changed(user_id)

    async def on_thread_members(self, event: hikari.ThreadMembersUpdateEvent) -> None:
        self.sink.thread_members_changed(int(event.thread_id))

    async def on_role(self, _event: hikari.RoleEvent) -> None:
        roles = [
            role_record(r) for r in self.sink.bot.cache.get_roles_view_for_guild(self.sink.config.guild_id).values()
        ]
        roles = [r for r in roles if r.id != self.sink.config.guild_id]
        await asyncio.to_thread(self.sink.db.write, lambda conn: writer.replace_roles(conn, roles))
        self.sink.structure_changed()
