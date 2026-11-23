from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import hikari
import pytest
from hikari.iterators import FlatLazyIterator

from zerya_discord_mcp.config import Config
from zerya_discord_mcp.index import writer
from zerya_discord_mcp.index.db import Database
from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord, MessageRecord
from zerya_discord_mcp.ingest.backfill import Backfill
from zerya_discord_mcp.ingest.sink import Sink
from zerya_discord_mcp.testing.fixtures import GUILD_ID
from zerya_discord_mcp.tests import payloads as p

OLA = p.user(1, "ola", global_name="Ola")
GENERAL, RANDOM, FORUM, POST, ARCHIVED, PRIVATE = 10, 11, 13, 20, 21, 22
START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class FakeCache:
    def get_member(self, guild: int, user: int) -> None:
        return None

    def get_user(self, user: int) -> None:
        return None

    def get_role(self, role: int) -> None:
        return None


class FakeRest:
    """The REST endpoints the backfill uses, over in-memory channels and messages.

    ``fetch_messages`` pages like Discord through hikari: ``before`` yields newest first, ``after`` oldest first.
    """

    def __init__(self) -> None:
        self.roles = [p.role(GUILD_ID, "@everyone"), p.role(50, "Zarząd", position=1)]
        self.members = [p.member(1, "ola", nick="Ola"), p.member(2, "wikipop", roles=(50,))]
        self.channels: dict[int, p.Json] = {}
        self.active_threads: dict[int, p.Json] = {}
        self.archived: dict[int, list[p.Json]] = {}
        self.private_archived: dict[int, list[p.Json]] = {}
        self.messages: dict[int, list[hikari.Message]] = {}
        self.forbidden_channels: set[int] = set()
        self.forbidden_archives: set[int] = set()
        self.broken_channels: set[int] = set()
        self.interrupt_after: dict[int, int] = {}
        self.calls: list[tuple[int, str, int]] = []
        self._serial = 0

    # Setup helpers ----------------------------------------------------------------------------------------

    def post(self, channel_id: int, at: datetime, content: str, **extra: Any) -> int:
        existing = self.messages.setdefault(channel_id, [])
        self._serial += 1  # unique across channels, like real snowflakes
        mid = p.message_id(at, self._serial)
        existing.append(p.message(mid, channel_id, OLA, content, **extra))
        existing.sort(key=lambda m: int(m.id))
        return mid

    def post_many(self, channel_id: int, count: int, start: datetime = START) -> list[int]:
        return [self.post(channel_id, start + timedelta(minutes=i), f"wiadomość {i}") for i in range(count)]

    def edit(self, channel_id: int, mid: int, content: str) -> None:
        messages = self.messages[channel_id]
        index = next(i for i, m in enumerate(messages) if int(m.id) == mid)
        messages[index] = p.message(mid, channel_id, OLA, content, edited_timestamp=p.TIMESTAMP)

    def remove(self, channel_id: int, mid: int) -> None:
        self.messages[channel_id] = [m for m in self.messages[channel_id] if int(m.id) != mid]

    def _last(self, payload: p.Json) -> p.Json:
        messages = self.messages.get(int(payload["id"]))
        return payload | {"last_message_id": str(int(messages[-1].id)) if messages else None}

    # hikari REST surface ----------------------------------------------------------------------------------

    async def fetch_roles(self, guild: int) -> list[hikari.Role]:
        return self.roles

    def fetch_members(self, guild: int) -> FlatLazyIterator[hikari.Member]:
        return FlatLazyIterator(self.members)

    async def fetch_guild_channels(self, guild: int) -> list[hikari.GuildChannel]:
        return [p.channel(self._last(c)) for c in self.channels.values()]

    async def fetch_active_threads(self, guild: int) -> list[hikari.GuildThreadChannel]:
        return [p.guild_thread(self._last(t)) for t in self.active_threads.values()]

    def _threads(self, source: dict[int, list[p.Json]], channel: hikari.GuildChannel) -> Iterator[Any]:
        if int(channel.id) in self.forbidden_archives:
            raise p.forbidden()
        yield from (p.guild_thread(self._last(t)) for t in source.get(int(channel.id), []))

    def fetch_public_archived_threads(self, channel: hikari.GuildChannel) -> FlatLazyIterator[Any]:
        return FlatLazyIterator(self._threads(self.archived, channel))

    def fetch_private_archived_threads(self, channel: hikari.GuildChannel) -> FlatLazyIterator[Any]:
        return FlatLazyIterator(self._threads(self.private_archived, channel))

    def fetch_messages(
        self,
        channel: int,
        *,
        before: hikari.UndefinedOr[hikari.SearchableSnowflakeish] = hikari.UNDEFINED,
        after: hikari.UndefinedOr[hikari.SearchableSnowflakeish] = hikari.UNDEFINED,
    ) -> FlatLazyIterator[hikari.Message]:
        channel_id = channel
        if after is not hikari.UNDEFINED:
            bound = cast(int, after)
            self.calls.append((channel_id, "after", bound))
            selected = [m for m in self.messages.get(channel_id, []) if int(m.id) > bound]
        else:
            bound = cast(int, before)
            self.calls.append((channel_id, "before", bound))
            selected = [m for m in reversed(self.messages.get(channel_id, [])) if int(m.id) < bound]

        def pages() -> Iterator[hikari.Message]:
            if channel_id in self.forbidden_channels:
                raise p.forbidden()
            if channel_id in self.broken_channels:
                raise RuntimeError("Discord returned garbage")
            limit = self.interrupt_after.pop(channel_id, None)
            for index, message in enumerate(selected):
                if limit is not None and index == limit:
                    raise RuntimeError("connection lost")
                yield message

        return FlatLazyIterator(pages())


class FakeBot:
    def __init__(self) -> None:
        self.rest = FakeRest()
        self.cache = FakeCache()


class Env:
    def __init__(self, tmp_path: Path, **config: Any) -> None:
        self.bot = FakeBot()
        self.rest = self.bot.rest
        self.db = Database(tmp_path / "index.sqlite3")
        self.db.open()
        self.config = Config(db_path=tmp_path / "index.sqlite3", guild_id=GUILD_ID, **config)
        self.sink = Sink(cast(hikari.GatewayBot, self.bot), self.db, self.config)

    def backfill(self) -> Backfill:
        return Backfill(self.sink)

    def message_ids(self, channel_id: int) -> list[int]:
        rows = self.db.reader().execute("SELECT id FROM messages WHERE channel_id = ? ORDER BY id", (channel_id,))
        return [int(r[0]) for r in rows]

    def content(self, mid: int) -> str:
        return str(self.db.reader().execute("SELECT content FROM messages WHERE id = ?", (mid,)).fetchone()[0])

    def cursor(self, channel_id: int) -> tuple[int | None, int | None, bool] | None:
        row = (
            self.db
            .reader()
            .execute("SELECT newest_id, oldest_id, complete FROM cursors WHERE channel_id = ?", (channel_id,))
            .fetchone()
        )
        return (row[0], row[1], bool(row[2])) if row else None

    def channel_ids(self) -> set[int]:
        return {int(r[0]) for r in self.db.reader().execute("SELECT id FROM channels")}


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    e.rest.channels[GENERAL] = p.text_channel(GENERAL, "general")
    yield e
    e.db.close()


async def test_initial_backfill_indexes_structure_and_every_channel(env: Env) -> None:
    rest = env.rest
    rest.channels[FORUM] = p.forum_channel(FORUM, "pomysly", tags={100: "idea"})
    rest.channels[1] = p.category(1, "Fundacja")
    rest.active_threads[POST] = p.thread(POST, "Nowe logo", FORUM, owner=1, tags=(100,))
    rest.archived[GENERAL] = [p.thread(ARCHIVED, "stary wątek", GENERAL, owner=1, archived=True)]
    rest.private_archived[GENERAL] = [p.thread(PRIVATE, "tajny", GENERAL, owner=2, private=True, archived=True)]
    general = rest.post_many(GENERAL, 250)
    post = rest.post_many(POST, 3)
    archived = rest.post_many(ARCHIVED, 2)
    private = rest.post_many(PRIVATE, 1)
    rest.post(GENERAL, START + timedelta(days=1), "Ola pinned a message", type=6)  # system message

    backfill = env.backfill()
    await backfill.run()

    assert backfill.done.is_set()
    assert backfill.failed_channels == 0
    assert backfill.indexed_messages == 256
    assert env.message_ids(GENERAL) == general
    assert env.message_ids(POST) == post
    assert env.message_ids(ARCHIVED) == archived
    assert env.message_ids(PRIVATE) == private
    assert env.channel_ids() == {1, GENERAL, FORUM, POST, ARCHIVED, PRIVATE}
    kinds = {cid: r.kind for cid, r in env.sink.channels.items()}
    assert kinds[POST] is ChannelKind.FORUM_POST and env.sink.channels[POST].tags == ("idea",)
    assert env.sink.channels[PRIVATE].is_private
    for channel_id in (GENERAL, POST, ARCHIVED, PRIVATE):
        cursor = env.cursor(channel_id)
        assert cursor is not None and cursor[2], channel_id
    assert env.cursor(FORUM) is None  # forums hold posts, not messages
    roles = env.db.reader().execute("SELECT id, name FROM roles").fetchall()
    assert roles == [(50, "Zarząd")]  # @everyone (the guild ID) is not a real role
    members = env.db.reader().execute("SELECT id, role_ids, in_guild FROM members ORDER BY id").fetchall()
    assert members == [(1, "", 1), (2, "50", 1)]


async def test_resume_after_interruption_continues_from_the_cursor(env: Env) -> None:
    rest = env.rest
    ids = rest.post_many(GENERAL, 250)
    rest.interrupt_after[GENERAL] = 150

    first = env.backfill()
    await first.run()
    assert first.failed_channels == 1
    assert env.message_ids(GENERAL) == ids[-100:]  # one full batch made it before the connection dropped
    assert env.cursor(GENERAL) == (ids[-1], ids[-100], False)

    rest.calls.clear()
    second = env.backfill()
    await second.run()
    assert second.failed_channels == 0
    assert env.message_ids(GENERAL) == ids
    assert rest.calls == [(GENERAL, "before", ids[-100])]  # nothing new, so no catch-up pass
    assert env.cursor(GENERAL) == (ids[-1], ids[0], True)


async def test_catch_up_fetches_only_messages_newer_than_the_cursor(env: Env) -> None:
    rest = env.rest
    old = rest.post_many(GENERAL, 5)
    await env.backfill().run()

    new = rest.post_many(GENERAL, 3, start=START + timedelta(days=2))
    rest.calls.clear()
    await env.backfill().run()

    assert rest.calls == [(GENERAL, "after", old[-1])]
    assert env.message_ids(GENERAL) == old + new
    assert env.cursor(GENERAL) == (new[-1], old[0], True)


async def test_catch_up_covers_channels_that_were_empty_at_the_first_backfill(env: Env) -> None:
    await env.backfill().run()
    assert env.cursor(GENERAL) == (None, None, True)

    new = env.rest.post_many(GENERAL, 2, start=START + timedelta(days=2))
    await env.backfill().run()
    assert env.message_ids(GENERAL) == new


async def test_backfill_since_stops_at_the_cut_off(tmp_path: Path) -> None:
    env = Env(tmp_path, backfill_since=date(2026, 9, 1), timezone="Europe/Warsaw")
    env.rest.channels[GENERAL] = p.text_channel(GENERAL, "general")
    old = env.rest.post_many(GENERAL, 3, start=datetime(2026, 8, 31, 12, 0, tzinfo=UTC))
    # 2026-09-01 00:30 in Warsaw is still August 31st in UTC: it counts as September 1st.
    new = env.rest.post_many(GENERAL, 3, start=datetime(2026, 8, 31, 22, 30, tzinfo=UTC))
    assert old[-1] < new[0]

    await env.backfill().run()
    assert env.message_ids(GENERAL) == new
    cursor = env.cursor(GENERAL)
    assert cursor is not None and cursor[2]
    env.db.close()


async def test_forbidden_channels_are_skipped_and_failures_counted(env: Env) -> None:
    rest = env.rest
    rest.channels[RANDOM] = p.text_channel(RANDOM, "random")
    rest.channels[12] = p.text_channel(12, "zepsuty")
    rest.post_many(GENERAL, 2)
    rest.post_many(RANDOM, 2)
    rest.post_many(12, 2)
    rest.forbidden_channels.add(RANDOM)
    rest.broken_channels.add(12)

    backfill = env.backfill()
    await backfill.run()

    assert len(env.message_ids(GENERAL)) == 2
    assert env.message_ids(RANDOM) == []
    assert env.cursor(RANDOM) is None
    # No access is expected (the bot is not in every channel); only real errors count as failed channels.
    assert backfill.failed_channels == 1
    assert backfill.indexed_messages == 2
    assert backfill.done.is_set()


def _stale_channel(env: Env) -> None:
    record = ChannelRecord(id=999, name="usuniety", kind=ChannelKind.TEXT)
    message = MessageRecord(id=5 << 30, channel_id=999, author_id=1, content="stara treść")
    env.db.write(lambda c: writer.upsert_channels(c, [record]))
    env.db.write(lambda c: writer.upsert_messages(c, [message]))


async def test_deleted_channels_are_removed_when_the_listing_is_complete(env: Env) -> None:
    _stale_channel(env)
    await env.backfill().run()
    assert 999 not in env.channel_ids()
    assert env.message_ids(999) == []


async def test_deleted_channels_are_kept_when_threads_could_not_be_listed(env: Env) -> None:
    _stale_channel(env)
    env.rest.forbidden_archives.add(GENERAL)
    await env.backfill().run()
    assert 999 in env.channel_ids()
    assert len(env.message_ids(999)) == 1


async def test_reconcile_applies_edits_and_deletes_but_keeps_newer_messages(env: Env) -> None:
    rest = env.rest
    now = datetime.now(UTC)
    ancient = rest.post(GENERAL, now - timedelta(days=30), "sprzed okna")
    recent = rest.post_many(GENERAL, 4, start=now - timedelta(days=2))
    backfill = env.backfill()
    await backfill.run()
    assert env.message_ids(GENERAL) == [ancient, *recent]

    rest.edit(GENERAL, recent[0], "poprawiona treść")
    rest.remove(GENERAL, recent[1])
    rest.remove(GENERAL, ancient)  # outside the window: left alone
    # Stored by a listener while the reconcile runs (newer than its start): not on the REST page it read.
    live = p.message_id(now + timedelta(hours=1))
    env.db.write(
        lambda c: writer.upsert_messages(
            c, [MessageRecord(id=live, channel_id=GENERAL, author_id=1, content="na żywo")]
        )
    )

    updated, deleted = await backfill.reconcile(days=7)

    assert (updated, deleted) == (3, 1)
    assert env.content(recent[0]) == "poprawiona treść"
    assert env.message_ids(GENERAL) == [ancient, recent[0], recent[2], recent[3], live]


async def test_reconcile_skips_channels_that_became_unreadable(env: Env) -> None:
    rest = env.rest
    now = datetime.now(UTC)
    rest.channels[RANDOM] = p.text_channel(RANDOM, "random")
    rest.post_many(GENERAL, 2, start=now - timedelta(days=1))
    kept = rest.post_many(RANDOM, 2, start=now - timedelta(days=1))
    backfill = env.backfill()
    await backfill.run()

    rest.forbidden_channels.add(RANDOM)
    rest.remove(GENERAL, int(rest.messages[GENERAL][0].id))
    assert await backfill.reconcile(days=7) == (1, 1)
    assert env.message_ids(RANDOM) == kept
