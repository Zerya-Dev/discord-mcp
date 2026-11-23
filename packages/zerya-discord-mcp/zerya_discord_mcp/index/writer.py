"""All writes to the index. Functions take a connection already inside a transaction (see Database.write)."""

import sqlite3
from collections.abc import Iterable, Sequence

from zerya_discord_mcp.index.models import ChannelRecord, MemberRecord, MessageRecord, RoleRecord
from zerya_discord_mcp.text import fts_fold, lemmas_for_index


def _ids(values: Iterable[int]) -> str:
    return " ".join(str(v) for v in values)


def upsert_messages(conn: sqlite3.Connection, messages: Sequence[MessageRecord]) -> None:
    """Inserts or overwrites messages and re-indexes their text (edits replace the old text everywhere)."""
    if not messages:
        return
    conn.executemany(
        """
        INSERT INTO messages (id, channel_id, author_id, reply_to_id, edited_at, content, mention_ids,
                              has_link, has_file, has_code, reactions)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            channel_id = excluded.channel_id, author_id = excluded.author_id, reply_to_id = excluded.reply_to_id,
            edited_at = excluded.edited_at, content = excluded.content, mention_ids = excluded.mention_ids,
            has_link = excluded.has_link, has_file = excluded.has_file, has_code = excluded.has_code,
            reactions = excluded.reactions
        """,
        [
            (
                m.id,
                m.channel_id,
                m.author_id,
                m.reply_to_id,
                m.edited_at,
                m.content,
                _ids(m.mention_ids),
                int(m.has_link),
                int(m.has_file),
                int(m.has_code),
                m.reactions,
            )
            for m in messages
        ],
    )
    rowids = [(m.id,) for m in messages]
    conn.executemany("DELETE FROM fts_words WHERE rowid = ?", rowids)
    conn.executemany("DELETE FROM fts_trigram WHERE rowid = ?", rowids)
    folded = [(m.id, fts_fold(m.content)) for m in messages]
    conn.executemany(
        "INSERT INTO fts_words (rowid, text, lemmas) VALUES (?, ?, ?)",
        [(mid, text, lemmas_for_index(text)) for mid, text in folded],
    )
    conn.executemany("INSERT INTO fts_trigram (rowid, text) VALUES (?, ?)", folded)


def delete_messages(conn: sqlite3.Connection, message_ids: Iterable[int]) -> None:
    """Removes messages and their text from the index. We never keep content its author deleted."""
    rows = [(mid,) for mid in message_ids]
    conn.executemany("DELETE FROM messages WHERE id = ?", rows)
    conn.executemany("DELETE FROM fts_words WHERE rowid = ?", rows)
    conn.executemany("DELETE FROM fts_trigram WHERE rowid = ?", rows)


def delete_channel(conn: sqlite3.Connection, channel_id: int) -> None:
    ids = [row[0] for row in conn.execute("SELECT id FROM messages WHERE channel_id = ?", (channel_id,))]
    delete_messages(conn, ids)
    conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    conn.execute("DELETE FROM cursors WHERE channel_id = ?", (channel_id,))


def upsert_channels(conn: sqlite3.Connection, channels: Sequence[ChannelRecord]) -> None:
    conn.executemany(
        """
        INSERT INTO channels (id, name, kind, parent_id, topic, tags, position, is_private, owner_id, archived)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            name = excluded.name, kind = excluded.kind, parent_id = excluded.parent_id, topic = excluded.topic,
            tags = excluded.tags, position = excluded.position, is_private = excluded.is_private,
            owner_id = excluded.owner_id, archived = excluded.archived
        """,
        [
            (
                c.id,
                c.name,
                c.kind.value,
                c.parent_id,
                c.topic,
                " ".join(c.tags),
                c.position,
                int(c.is_private),
                c.owner_id,
                int(c.archived),
            )
            for c in channels
        ],
    )


def upsert_members(conn: sqlite3.Connection, members: Sequence[MemberRecord]) -> None:
    conn.executemany(
        """
        INSERT INTO members (id, username, display_name, nick, role_ids, is_bot, in_guild)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            username = excluded.username, display_name = excluded.display_name, nick = excluded.nick,
            role_ids = excluded.role_ids, is_bot = excluded.is_bot, in_guild = excluded.in_guild
        """,
        [(m.id, m.username, m.display_name, m.nick, _ids(m.role_ids), int(m.is_bot), int(m.in_guild)) for m in members],
    )


def upsert_authors(conn: sqlite3.Connection, members: Sequence[MemberRecord]) -> None:
    """Records message authors without overwriting richer data we already have (e.g. people who left)."""
    conn.executemany(
        """
        INSERT INTO members (id, username, display_name, nick, role_ids, is_bot, in_guild)
        VALUES (?, ?, ?, ?, '', ?, 0)
        ON CONFLICT (id) DO UPDATE SET
            username = excluded.username,
            display_name = coalesce(excluded.display_name, members.display_name),
            nick = coalesce(excluded.nick, members.nick)
        """,
        [(m.id, m.username, m.display_name, m.nick, int(m.is_bot)) for m in members],
    )


def mark_member_left(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("UPDATE members SET in_guild = 0 WHERE id = ?", (user_id,))


def replace_roles(conn: sqlite3.Connection, roles: Sequence[RoleRecord]) -> None:
    conn.execute("DELETE FROM roles")
    conn.executemany(
        "INSERT INTO roles (id, name, position) VALUES (?, ?, ?)", [(r.id, r.name, r.position) for r in roles]
    )


def save_cursor(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    newest_id: int | None = None,
    oldest_id: int | None = None,
    complete: bool | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO cursors (channel_id, newest_id, oldest_id, complete) VALUES (?, ?, ?, coalesce(?, 0))
        ON CONFLICT (channel_id) DO UPDATE SET
            newest_id = CASE
                WHEN excluded.newest_id IS NULL THEN cursors.newest_id
                WHEN cursors.newest_id IS NULL THEN excluded.newest_id
                ELSE max(cursors.newest_id, excluded.newest_id) END,
            oldest_id = CASE
                WHEN excluded.oldest_id IS NULL THEN cursors.oldest_id
                WHEN cursors.oldest_id IS NULL THEN excluded.oldest_id
                ELSE min(cursors.oldest_id, excluded.oldest_id) END,
            complete = coalesce(?, cursors.complete)
        """,
        (
            channel_id,
            newest_id,
            oldest_id,
            None if complete is None else int(complete),
            None if complete is None else int(complete),
        ),
    )
