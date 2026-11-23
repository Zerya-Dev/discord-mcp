"""Rendering messages as compact text for agents.

Message content is untrusted: anyone on the server can write instructions into a message. All content goes
inside explicit delimiters and the delimiter itself is neutralised inside content.
"""

import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from zerya_discord_mcp.index.models import snowflake_time
from zerya_discord_mcp.query.directory import Directory

UNTRUSTED_OPEN = "<untrusted_discord_messages>"
UNTRUSTED_CLOSE = "</untrusted_discord_messages>"
_DELIMITER_RE = re.compile(r"</?\s*untrusted_discord_messages\s*>", re.IGNORECASE)
_NEWLINES_RE = re.compile(r"\s*\n\s*")
_CHANNEL_MENTION_RE = re.compile(r"<#(\d+)>")


@dataclass(frozen=True, slots=True)
class MessageRow:
    id: int
    channel_id: int
    author_id: int
    reply_to_id: int | None
    edited: bool
    content: str


MESSAGE_COLUMNS = "id, channel_id, author_id, reply_to_id, edited_at, content"


def message_from_row(row: tuple[object, ...]) -> MessageRow:
    mid, cid, aid, reply, edited, content = row
    return MessageRow(
        id=int(str(mid)),
        channel_id=int(str(cid)),
        author_id=int(str(aid)),
        reply_to_id=int(str(reply)) if reply is not None else None,
        edited=edited is not None,
        content=str(content),
    )


def fetch_range(conn: sqlite3.Connection, channel_id: int, first_id: int, last_id: int) -> list[MessageRow]:
    rows = conn.execute(
        f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE channel_id = ? AND id BETWEEN ? AND ? ORDER BY id",
        (channel_id, first_id, last_id),
    )
    return [message_from_row(r) for r in rows]


def fetch_before(conn: sqlite3.Connection, channel_id: int, before_id: int, limit: int) -> list[MessageRow]:
    rows = conn.execute(
        f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE channel_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
        (channel_id, before_id, limit),
    )
    return [message_from_row(r) for r in reversed(list(rows))]


def fetch_after(conn: sqlite3.Connection, channel_id: int, after_id: int, limit: int) -> list[MessageRow]:
    rows = conn.execute(
        f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE channel_id = ? AND id > ? ORDER BY id LIMIT ?",
        (channel_id, after_id, limit),
    )
    return [message_from_row(r) for r in rows]


def resolve_channel_mentions(content: str, directory: Directory) -> str:
    """<#id> → #name for channels the reader can see, #private-channel otherwise."""

    def name(match: re.Match[str]) -> str:
        channel_id = int(match.group(1))
        if channel_id in directory.allowed:
            return directory.channel_label(channel_id)
        return "#private-channel"

    return _CHANNEL_MENTION_RE.sub(name, content)


def sanitize(content: str, max_chars: int | None = None) -> str:
    text = _DELIMITER_RE.sub("[delimiter removed]", content)
    text = _NEWLINES_RE.sub(" ↵ ", text.strip())
    if max_chars is not None and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text or "(no text)"


def message_link(guild_id: int, channel_id: int, message_id: int | None = None) -> str:
    base = f"https://discord.com/channels/{guild_id}/{channel_id}"
    return f"{base}/{message_id}" if message_id else base


class Formatter:
    def __init__(self, directory: Directory, tz: ZoneInfo, guild_id: int) -> None:
        self.directory = directory
        self.tz = tz
        self.guild_id = guild_id

    def local(self, message_id: int) -> datetime:
        return snowflake_time(message_id).astimezone(self.tz)

    def date(self, message_id: int) -> str:
        return self.local(message_id).strftime("%Y-%m-%d")

    def participants(self, messages: Iterable[MessageRow], limit: int = 5) -> str:
        counts = Counter(m.author_id for m in messages)
        names = [self.directory.member_name(uid) for uid, _ in counts.most_common(limit)]
        extra = len(counts) - len(names)
        return ", ".join(names) + (f" +{extra}" if extra > 0 else "")

    def lines(
        self,
        messages: Sequence[MessageRow],
        *,
        focal: set[int] | frozenset[int] = frozenset(),
        max_chars: int | None = None,
        focal_max_chars: int | None = None,
        start_date: str | None = None,
    ) -> list[str]:
        out: list[str] = []
        current_date = start_date
        ids = {m.id for m in messages}
        previous: int | None = None
        for message in messages:
            day = self.date(message.id)
            if day != current_date:
                if current_date is not None:
                    out.append(f"  — {day} —")
                current_date = day
            marker = "►" if message.id in focal else " "
            limit = focal_max_chars if message.id in focal else max_chars
            reply = ""
            if message.reply_to_id is not None and message.reply_to_id != previous:
                if message.reply_to_id in ids:
                    reply = f"(↪ {self.local(message.reply_to_id).strftime('%H:%M')}) "
                else:
                    reply = "(↪ earlier msg) "
            edited = " (edited)" if message.edited else ""
            time = self.local(message.id).strftime("%H:%M")
            author = self.directory.member_name(message.author_id)
            text = sanitize(resolve_channel_mentions(message.content, self.directory), limit)
            out.append(f"{marker} {time} {author}: {reply}{text}{edited}")
            previous = message.id
        return out


def collapse_gaps(messages: list[MessageRow], focal: set[int], keep: int) -> list[MessageRow | int]:
    """Keeps ``keep`` messages around focal ones; longer runs between them become a skipped-count marker."""
    if not focal:
        return list(messages)
    indexes = [i for i, m in enumerate(messages) if m.id in focal]
    visible: set[int] = set()
    for i in indexes:
        visible.update(range(max(0, i - keep), min(len(messages), i + keep + 1)))
    out: list[MessageRow | int] = []
    skipped = 0
    for i, message in enumerate(messages):
        if i in visible:
            if skipped:
                out.append(skipped)
                skipped = 0
            out.append(message)
        else:
            skipped += 1
    if skipped:
        out.append(skipped)
    return out
