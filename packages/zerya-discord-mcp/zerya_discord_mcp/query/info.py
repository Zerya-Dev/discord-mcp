"""The info tool: a channel, a person or the whole server (only what the user may read)."""

import json
import sqlite3
import time
from collections import defaultdict
from zoneinfo import ZoneInfo

from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord, snowflake_at
from zerya_discord_mcp.query.directory import MENTION_RE, Directory, parse_link, safe_name
from zerya_discord_mcp.query.parser import QueryError
from zerya_discord_mcp.query.render import UNTRUSTED_CLOSE, UNTRUSTED_OPEN, Formatter, sanitize
from zerya_discord_mcp.text import fold

SERVER_WORDS = frozenset({"", "server", "guild", "serwer", "*"})


def _count(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class Info:
    def __init__(self, tz: ZoneInfo, guild_id: int) -> None:
        self.tz = tz
        self.guild_id = guild_id

    def info(self, conn: sqlite3.Connection, allowed: frozenset[int], target: str | None) -> str:
        directory = Directory(conn, allowed)
        ref = (target or "").strip()
        if fold(ref) in SERVER_WORDS:
            return self._server(conn, directory)
        if ref.startswith("@") or (MENTION_RE.match(ref) and ref.startswith("<@")):
            return self._person(conn, directory, ref)
        if ref.startswith("#") or ref.startswith("<#") or parse_link(ref):
            return self._channel(conn, directory, directory.resolve_channel(ref))
        if ref.isdigit():
            if int(ref) in directory.channels and int(ref) in allowed:
                return self._channel(conn, directory, directory.channels[int(ref)])
            return self._person(conn, directory, ref)
        try:
            return self._channel(conn, directory, directory.resolve_channel(ref))
        except QueryError as channel_error:
            try:
                return self._person(conn, directory, ref)
            except QueryError:
                raise channel_error from None

    def _stats(self, conn: sqlite3.Connection, channel_ids: list[int]) -> dict[int, tuple[int, int, int]]:
        rows = conn.execute(
            "SELECT channel_id, count(*), min(id), max(id) FROM messages "
            "WHERE channel_id IN (SELECT value FROM json_each(?)) GROUP BY channel_id",
            (json.dumps(channel_ids),),
        )
        return {int(c): (int(n), int(lo), int(hi)) for c, n, lo, hi in rows}

    def _server(self, conn: sqlite3.Connection, directory: Directory) -> str:
        fmt = Formatter(directory, self.tz, self.guild_id)
        readable = directory.readable_channels()
        stats = self._stats(conn, [c.id for c in readable])
        threads: defaultdict[int, list[ChannelRecord]] = defaultdict(list)
        top_level: list[ChannelRecord] = []
        for channel in readable:
            if channel.kind.is_thread and channel.parent_id is not None:
                threads[channel.parent_id].append(channel)
            else:
                top_level.append(channel)

        by_category: defaultdict[int | None, list[ChannelRecord]] = defaultdict(list)
        for channel in top_level:
            by_category[channel.parent_id].append(channel)
        categories = sorted(
            (c for c in directory.channels.values() if c.kind is ChannelKind.CATEGORY and c.id in by_category),
            key=lambda c: c.position,
        )
        total = sum(n for n, _, _ in stats.values())
        lines = [f"Server map: {len(readable)} readable channels/threads, {_count(total)} indexed messages."]
        sections: list[tuple[str, list[ChannelRecord]]] = [("(no category)", by_category.get(None, []))]
        sections += [(safe_name(c.name), by_category[c.id]) for c in categories]
        for title, channels in sections:
            if not channels:
                continue
            lines.append(f"{title}:")
            for channel in sorted(channels, key=lambda c: c.position):
                n, _, last = stats.get(channel.id, (0, 0, 0))
                child = threads.get(channel.id, [])
                child_msgs = sum(stats.get(t.id, (0, 0, 0))[0] for t in child)
                last = max([last] + [stats.get(t.id, (0, 0, 0))[2] for t in child])
                parts = [f"{_count(n + child_msgs)} msgs"]
                if child:
                    parts.append(f"{len(child)} {'posts' if channel.kind is ChannelKind.FORUM else 'threads'}")
                if last:
                    parts.append(f"last {fmt.date(last)}")
                kind = "" if channel.kind is ChannelKind.TEXT else f" [{channel.kind.value}]"
                lines.append(f"  {directory.channel_label(channel.id)}{kind} · {' · '.join(parts)}")
        lines.append("Use info(#channel) for details or search with in:#channel.")
        return "\n".join(lines)

    def _channel(self, conn: sqlite3.Connection, directory: Directory, channel: ChannelRecord) -> str:
        fmt = Formatter(directory, self.tz, self.guild_id)
        children = directory.children(channel.id)
        ids = [channel.id] + [c.id for c in children]
        stats = self._stats(conn, ids)
        n = sum(s[0] for s in stats.values())
        lines = [f"{directory.channel_label(channel.id)} [{channel.kind.value}] · {_count(n)} indexed messages"]
        if channel.parent_id and channel.parent_id in directory.channels:
            parent = directory.channels[channel.parent_id]
            label = "category" if parent.kind is ChannelKind.CATEGORY else "parent"
            lines.append(f"{label}: {safe_name(parent.name)}")
        if stats:
            first = min(s[1] for s in stats.values())
            last = max(s[2] for s in stats.values())
            lines.append(f"Activity: {fmt.date(first)} – {fmt.date(last)}")
        untrusted: list[str] = []
        if channel.topic:
            untrusted.append(f"Topic: {sanitize(channel.topic, 500)}")
        if channel.tags:
            untrusted.append(f"Tags: {', '.join(channel.tags)}")

        since = snowflake_at_days_ago(90)
        for label, low in (("Top authors (90 days)", since), ("Top authors (all time)", 0)):
            rows = conn.execute(
                "SELECT author_id, count(*) AS n FROM messages WHERE channel_id IN (SELECT value FROM json_each(?)) "
                "AND id >= ? GROUP BY author_id ORDER BY n DESC LIMIT 6",
                (json.dumps(ids), low),
            ).fetchall()
            if rows:
                lines.append(f"{label}: " + ", ".join(f"{directory.member_name(int(a))} {c}" for a, c in rows))

        months = conn.execute(
            "SELECT id FROM messages WHERE channel_id IN (SELECT value FROM json_each(?)) AND id >= ?",
            (json.dumps(ids), snowflake_at_days_ago(180)),
        ).fetchall()
        per_month: defaultdict[str, int] = defaultdict(int)
        for (mid,) in months:
            per_month[fmt.local(int(mid)).strftime("%Y-%m")] += 1
        if per_month:
            lines.append("Messages per month: " + ", ".join(f"{m} {c}" for m, c in sorted(per_month.items())))

        if children:
            children.sort(key=lambda c: -stats.get(c.id, (0, 0, 0))[2])
            kind = "Posts" if channel.kind is ChannelKind.FORUM else "Threads"
            untrusted.append(f"{kind} ({len(children)}, latest activity first):")
            for child in children[:15]:
                cn, _, clast = stats.get(child.id, (0, 0, 0))
                when = fmt.date(clast) if clast else "-"
                untrusted.append(f"  - {sanitize(child.name, 100)} · {cn} msgs · last {when} · id {child.id}")
        if untrusted:
            lines += [UNTRUSTED_OPEN, *untrusted, UNTRUSTED_CLOSE]
        return "\n".join(lines)

    def _person(self, conn: sqlite3.Connection, directory: Directory, ref: str) -> str:
        fmt = Formatter(directory, self.tz, self.guild_id)
        member = directory.resolve_member(ref)
        names = list(
            dict.fromkeys(safe_name(n, 40) for n in (member.nick, member.display_name) if n and n != member.username)
        )
        lines = [
            f"@{member.username}" + (f" ({', '.join(names)})" if names else "") + (" [bot]" if member.is_bot else "")
        ]
        if not member.in_guild:
            lines.append("No longer on the server.")
        role_rows = conn.execute(
            "SELECT name FROM roles WHERE id IN (SELECT value FROM json_each(?)) ORDER BY position DESC",
            (json.dumps(list(member.role_ids)),),
        ).fetchall()
        if role_rows:
            lines.append("Roles: " + ", ".join(str(r[0]) for r in role_rows))
        allowed = json.dumps(sorted(directory.allowed))
        total = conn.execute(
            "SELECT count(*), min(id), max(id) FROM messages WHERE author_id = ? "
            "AND channel_id IN (SELECT value FROM json_each(?))",
            (member.id, allowed),
        ).fetchone()
        if total and total[0]:
            lines.append(f"Messages: {_count(int(total[0]))} · {fmt.date(int(total[1]))} – {fmt.date(int(total[2]))}")
        for label, low in (("Writes in (90 days)", snowflake_at_days_ago(90)), ("Writes in (all time)", 0)):
            rows = conn.execute(
                "SELECT channel_id, count(*) AS n FROM messages WHERE author_id = ? AND id >= ? "
                "AND channel_id IN (SELECT value FROM json_each(?)) GROUP BY channel_id ORDER BY n DESC LIMIT 8",
                (member.id, low, allowed),
            ).fetchall()
            if rows:
                lines.append(f"{label}: " + ", ".join(f"{directory.channel_label(int(c))} {n}" for c, n in rows))
        lines.append(f"Search their messages with from:{member.username}.")
        return "\n".join(lines)


def snowflake_at_days_ago(days: int) -> int:
    from datetime import UTC, datetime

    return snowflake_at(datetime.fromtimestamp(time.time() - days * 86400, tz=UTC))
