"""The read tool: messages in order around a message, from a date, or the latest ones in a channel."""

import sqlite3
from zoneinfo import ZoneInfo

from zerya_discord_mcp.index.models import ChannelKind, snowflake_at
from zerya_discord_mcp.query.directory import Directory, parse_link, safe_name
from zerya_discord_mcp.query.parser import QueryError
from zerya_discord_mcp.query.planner import parse_date
from zerya_discord_mcp.query.render import (
    MESSAGE_COLUMNS,
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    Formatter,
    MessageRow,
    fetch_after,
    fetch_before,
    message_from_row,
    message_link,
)
from zerya_discord_mcp.text import estimate_tokens

DEFAULT_AROUND = 10
DEFAULT_LATEST = 40
MAX_MESSAGES = 200
READ_MAX_CHARS = 2000


def _cursor(channel_id: int, message_id: int, direction: str) -> str:
    return f"r{direction}{channel_id}.{message_id}"


def _parse_cursor(cursor: str) -> tuple[int, int, str]:
    direction = cursor[1:2]
    channel, _, message = cursor[2:].partition(".")
    if not cursor.startswith("r") or direction not in ("<", ">") or not channel.isdigit() or not message.isdigit():
        raise QueryError("Bad cursor. Use a cursor exactly as returned by a previous read.")
    return int(channel), int(message), direction


class Reader:
    def __init__(self, tz: ZoneInfo, guild_id: int) -> None:
        self.tz = tz
        self.guild_id = guild_id

    def read(
        self,
        conn: sqlite3.Connection,
        allowed: frozenset[int],
        target: str | None = None,
        *,
        before: int | None = None,
        after: int | None = None,
        since: str | None = None,
        cursor: str | None = None,
        budget_tokens: int = 6000,
    ) -> str:
        directory = Directory(conn, allowed)
        fmt = Formatter(directory, self.tz, self.guild_id)
        anchor: int | None = None

        if cursor:
            channel_id, edge, direction = _parse_cursor(cursor)
            if channel_id not in allowed:
                raise QueryError("This cursor points to a channel you cannot read.")
            if direction == "<":
                messages = fetch_before(conn, channel_id, edge, min(before or DEFAULT_LATEST, MAX_MESSAGES))
            else:
                messages = fetch_after(conn, channel_id, edge, min(after or DEFAULT_LATEST, MAX_MESSAGES))
        else:
            if not target:
                raise QueryError("Give a target: a message link, a message ID, #channel, a thread link or an ID.")
            channel_id, anchor = self._resolve_target(conn, directory, target)
            channel = directory.channels[channel_id]
            if channel.kind is ChannelKind.FORUM and anchor is None:
                return self._forum_posts(conn, directory, channel_id)
            if anchor is not None:
                n_before = min(DEFAULT_AROUND if before is None else before, MAX_MESSAGES)
                n_after = min(DEFAULT_AROUND if after is None else after, MAX_MESSAGES)
                messages = fetch_before(conn, channel_id, anchor, n_before)
                messages += self._fetch_one(conn, anchor)
                messages += fetch_after(conn, channel_id, anchor, n_after)
            elif since:
                start = snowflake_at(parse_date(since, self.tz, end=False))
                messages = fetch_after(conn, channel_id, start - 1, min(after or DEFAULT_LATEST, MAX_MESSAGES))
            else:
                latest = conn.execute("SELECT max(id) FROM messages WHERE channel_id = ?", (channel_id,)).fetchone()[0]
                if latest is None:
                    return f"{directory.channel_label(channel_id)} has no indexed messages."
                n = min(before or DEFAULT_LATEST, MAX_MESSAGES)
                messages = fetch_before(conn, channel_id, int(latest) + 1, n)

        if not messages:
            return f"No messages there in {directory.channel_label(channel_id)}."
        messages = self._fit(messages, anchor, budget_tokens)
        focal = {anchor} if anchor is not None else set[int]()
        lines = fmt.lines(messages, focal=focal, max_chars=READ_MAX_CHARS, focal_max_chars=READ_MAX_CHARS)
        first, last = messages[0].id, messages[-1].id
        has_older = conn.execute(
            "SELECT 1 FROM messages WHERE channel_id = ? AND id < ? LIMIT 1", (channel_id, first)
        ).fetchone()
        has_newer = conn.execute(
            "SELECT 1 FROM messages WHERE channel_id = ? AND id > ? LIMIT 1", (channel_id, last)
        ).fetchone()
        header = [
            f"{directory.channel_label(channel_id)} · {len(messages)} messages · "
            f"{fmt.local(first).strftime('%Y-%m-%d %H:%M')} – {fmt.local(last).strftime('%Y-%m-%d %H:%M')}",
            f"Link: {message_link(self.guild_id, channel_id, anchor or first)}",
        ]
        paging: list[str] = []
        if has_older:
            paging.append(f"older: {_cursor(channel_id, first, '<')}")
        if has_newer:
            paging.append(f"newer: {_cursor(channel_id, last, '>')}")
        if paging:
            header.append("Cursors: " + " · ".join(paging))
        start_line = f"  — {fmt.date(first)} —"
        return "\n".join([*header, UNTRUSTED_OPEN, start_line, *lines, UNTRUSTED_CLOSE])

    @staticmethod
    def _fetch_one(conn: sqlite3.Connection, message_id: int) -> list[MessageRow]:
        return [
            message_from_row(r)
            for r in conn.execute(f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE id = ?", (message_id,))
        ]

    @staticmethod
    def _fit(messages: list[MessageRow], anchor: int | None, budget_tokens: int) -> list[MessageRow]:
        """Drops messages farthest from the anchor (or the oldest ones) until the page fits the budget."""
        costs = [estimate_tokens(m.content[:READ_MAX_CHARS]) + 8 for m in messages]
        if sum(costs) <= budget_tokens:
            return messages
        if anchor is None:
            keep: list[MessageRow] = []
            used = 0
            for message, cost in zip(reversed(messages), reversed(costs), strict=True):
                if keep and used + cost > budget_tokens:
                    break
                keep.append(message)
                used += cost
            return list(reversed(keep))
        center = next((i for i, m in enumerate(messages) if m.id == anchor), len(messages) // 2)
        lo = hi = center
        used = costs[center]
        while True:
            grew = False
            if lo > 0 and used + costs[lo - 1] <= budget_tokens:
                lo -= 1
                used += costs[lo]
                grew = True
            if hi < len(messages) - 1 and used + costs[hi + 1] <= budget_tokens:
                hi += 1
                used += costs[hi]
                grew = True
            if not grew:
                break
        return messages[lo : hi + 1]

    def _resolve_target(self, conn: sqlite3.Connection, directory: Directory, target: str) -> tuple[int, int | None]:
        target = target.strip()
        link = parse_link(target)
        if link:
            if link.channel_id not in directory.allowed:
                raise QueryError("Unknown channel in that link (it does not exist or you cannot read it).")
            return link.channel_id, link.message_id
        if target.isdigit():
            row = conn.execute("SELECT channel_id FROM messages WHERE id = ?", (int(target),)).fetchone()
            # A message in a hidden channel gets the same answer as a missing one: no probing other channels.
            if row is not None and int(row[0]) in directory.allowed:
                return int(row[0]), int(target)
        return directory.resolve_channel(target).id, None

    def _forum_posts(self, conn: sqlite3.Connection, directory: Directory, forum_id: int) -> str:
        posts = directory.children(forum_id)
        stats = {
            int(cid): (int(n), int(last))
            for cid, n, last in conn.execute(
                "SELECT channel_id, count(*), max(id) FROM messages WHERE channel_id IN "
                f"({','.join(str(p.id) for p in posts) or 'NULL'}) GROUP BY channel_id"
            )
        }
        posts.sort(key=lambda p: -stats.get(p.id, (0, 0))[1])
        fmt = Formatter(directory, self.tz, self.guild_id)
        lines = [
            f"{directory.channel_label(forum_id)} is a forum with {len(posts)} readable posts (newest activity first):"
        ]
        for post in posts[:60]:
            n, last = stats.get(post.id, (0, 0))
            when = fmt.date(last) if last else "-"
            tags = f" [{', '.join(safe_name(t) for t in post.tags)}]" if post.tags else ""
            lines.append(f"- {safe_name(post.name, 100)}{tags} · {n} msgs · last {when} · id {post.id}")
        lines.append("Read a post with read(target=<id or link>).")
        return "\n".join(lines)
