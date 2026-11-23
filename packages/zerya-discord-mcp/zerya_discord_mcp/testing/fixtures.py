"""Builders for test indexes: fake channels, members and messages straight into SQLite (no Discord)."""

import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

from zerya_discord_mcp.index import writer
from zerya_discord_mcp.index.db import Database
from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord, MemberRecord, MessageRecord, snowflake_at

GUILD_ID = 1000


class IndexBuilder:
    def __init__(self, path: Path, start: datetime | None = None) -> None:
        self.db = Database(path)
        self.db.open()
        self.clock = start or datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
        self._ids = itertools.count(1)
        self._names: dict[str, int] = {}

    def channel(
        self,
        name: str,
        kind: ChannelKind = ChannelKind.TEXT,
        parent: int | None = None,
        *,
        channel_id: int | None = None,
        is_private: bool = False,
        owner_id: int | None = None,
    ) -> int:
        cid = channel_id or 100 + len(self._names)
        record = ChannelRecord(id=cid, name=name, kind=kind, parent_id=parent, is_private=is_private, owner_id=owner_id)
        self.db.write(lambda conn: writer.upsert_channels(conn, [record]))
        self._names[name] = cid
        return cid

    def member(self, uid: int, username: str, nick: str | None = None, display_name: str | None = None) -> int:
        record = MemberRecord(id=uid, username=username, nick=nick, display_name=display_name)
        self.db.write(lambda conn: writer.upsert_members(conn, [record]))
        return uid

    def say(self, channel_id: int, author_id: int, content: str, *, gap: timedelta = timedelta(minutes=2)) -> int:
        self.clock += gap
        mid = snowflake_at(self.clock) + next(self._ids)
        record = MessageRecord(id=mid, channel_id=channel_id, author_id=author_id, content=content)
        self.db.write(lambda conn: writer.upsert_messages(conn, [record]))
        return mid

    def all_channels(self) -> frozenset[int]:
        return frozenset(int(r[0]) for r in self.db.reader().execute("SELECT id FROM channels"))
