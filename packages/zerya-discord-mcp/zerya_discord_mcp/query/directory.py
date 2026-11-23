"""Names of channels and people: resolving what a human (or agent) types into IDs, and IDs into display names.

Channel lookups only ever consider channels the user may read, so suggestions never reveal hidden channels.
"""

import re
import sqlite3
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord, MemberRecord
from zerya_discord_mcp.query.parser import QueryError
from zerya_discord_mcp.text import fold

LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+|@me)/(\d+)(?:/(\d+))?")
MENTION_RE = re.compile(r"^<[@#][!&]?(\d+)>$")


@dataclass(frozen=True, slots=True)
class LinkRef:
    channel_id: int
    message_id: int | None


def parse_link(text: str) -> LinkRef | None:
    match = LINK_RE.search(text)
    if not match:
        return None
    return LinkRef(channel_id=int(match.group(2)), message_id=int(match.group(3)) if match.group(3) else None)


_UNSAFE_NAME_RE = re.compile(r"</?\s*untrusted_discord_messages\s*>|[<>`\n\r]", re.IGNORECASE)


def safe_name(name: str, limit: int = 60) -> str:
    """Display names and thread titles are written by members: one line, no markup, bounded length."""
    text = " ".join(_UNSAFE_NAME_RE.sub(" ", name).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _norm_name(name: str) -> str:
    return fold(name.strip().lstrip("#@").strip())


def _channel_from_row(row: sqlite3.Row | tuple[object, ...]) -> ChannelRecord:
    cid, name, kind, parent_id, topic, tags, position, is_private, owner_id, archived = row
    return ChannelRecord(
        id=int(str(cid)),
        name=str(name),
        kind=ChannelKind(str(kind)),
        parent_id=int(str(parent_id)) if parent_id is not None else None,
        topic=str(topic) if topic else None,
        tags=tuple(str(tags).split()) if tags else (),
        position=int(str(position)),
        is_private=bool(is_private),
        owner_id=int(str(owner_id)) if owner_id is not None else None,
        archived=bool(archived),
    )


def _member_from_row(row: tuple[object, ...]) -> MemberRecord:
    mid, username, display_name, nick, role_ids, is_bot, in_guild = row
    return MemberRecord(
        id=int(str(mid)),
        username=str(username),
        display_name=str(display_name) if display_name else None,
        nick=str(nick) if nick else None,
        role_ids=tuple(int(r) for r in str(role_ids).split()),
        is_bot=bool(is_bot),
        in_guild=bool(in_guild),
    )


def load_channels(conn: sqlite3.Connection) -> dict[int, ChannelRecord]:
    rows = conn.execute(
        "SELECT id, name, kind, parent_id, topic, tags, position, is_private, owner_id, archived FROM channels"
    )
    return {c.id: c for c in map(_channel_from_row, rows)}


class Directory:
    def __init__(self, conn: sqlite3.Connection, allowed: frozenset[int]) -> None:
        self.allowed = allowed
        # Categories carry no messages but give structure to the server map.
        self.channels: dict[int, ChannelRecord] = {
            cid: c for cid, c in load_channels(conn).items() if cid in allowed or c.kind is ChannelKind.CATEGORY
        }
        self._members: dict[int, MemberRecord] | None = None
        self._conn = conn

    # Channels -----------------------------------------------------------------------------------------------

    def channel_label(self, channel_id: int) -> str:
        channel = self.channels.get(channel_id)
        if channel is None:
            return f"#{channel_id}"
        if channel.kind.is_thread and channel.parent_id is not None:
            parent = self.channels.get(channel.parent_id)
            parent_name = parent.name if parent else str(channel.parent_id)
            return f"#{safe_name(parent_name)} › {safe_name(channel.name)}"
        return f"#{safe_name(channel.name)}"

    def readable_channels(self) -> list[ChannelRecord]:
        return [c for c in self.channels.values() if c.id in self.allowed and c.kind is not ChannelKind.CATEGORY]

    def children(self, channel_id: int) -> list[ChannelRecord]:
        return [c for c in self.channels.values() if c.parent_id == channel_id and c.id in self.allowed]

    def resolve_channel(self, ref: str) -> ChannelRecord:
        """Accepts #name, name, a thread title, an ID, <#id> or a channel/message link."""
        ref = ref.strip()
        link = parse_link(ref)
        mention = MENTION_RE.match(ref)
        target_id: int | None = None
        if link:
            target_id = link.channel_id
        elif mention:
            target_id = int(mention.group(1))
        elif ref.isdigit():
            target_id = int(ref)
        if target_id is not None:
            channel = self.channels.get(target_id)
            if channel is None or channel.id not in self.allowed:
                raise QueryError(f"Unknown channel {ref} (it does not exist or you cannot read it).")
            return channel

        wanted = _norm_name(ref)
        candidates = self.readable_channels()
        exact = [c for c in candidates if _norm_name(c.name) == wanted]
        if exact:
            # Prefer real channels over threads with the same title.
            exact.sort(key=lambda c: (c.kind.is_thread, c.position))
            return exact[0]
        hint = self.suggest_channels(ref)
        suffix = f" Did you mean {', '.join(hint)}?" if hint else " Use the info tool to see the channel map."
        raise QueryError(f"Unknown channel {ref}.{suffix}")

    def suggest_channels(self, ref: str, limit: int = 3) -> list[str]:
        names = {c.id: _norm_name(c.name) for c in self.readable_channels()}
        matches = process.extract(_norm_name(ref), names, scorer=fuzz.WRatio, limit=limit, score_cutoff=60)
        return [self.channel_label(int(cid)) for _, _, cid in matches]

    def channels_matching_word(self, word: str) -> list[int]:
        """Readable channels whose name or thread title contains the folded word as a whole word."""
        out: list[int] = []
        for channel in self.readable_channels():
            tokens = re.split(r"[\W_]+", fold(channel.name))
            if word in tokens:
                out.append(channel.id)
        return out

    # Members ------------------------------------------------------------------------------------------------

    @property
    def members(self) -> dict[int, MemberRecord]:
        if self._members is None:
            self._members = {
                m.id: m
                for m in map(
                    _member_from_row,
                    self._conn.execute(
                        "SELECT id, username, display_name, nick, role_ids, is_bot, in_guild FROM members"
                    ),
                )
            }
        return self._members

    def member_name(self, user_id: int) -> str:
        member = self.members.get(user_id)
        return safe_name(member.name, 40) if member else f"user {user_id}"

    def resolve_member(self, ref: str) -> MemberRecord:
        """Accepts @nick, display name, username, an ID or <@id>."""
        ref = ref.strip()
        mention = MENTION_RE.match(ref)
        if mention or ref.isdigit():
            uid = int(mention.group(1) if mention else ref)
            member = self.members.get(uid)
            if member is None:
                raise QueryError(f"Unknown person {ref}.")
            return member
        wanted = _norm_name(ref)
        scored: list[tuple[int, MemberRecord]] = []
        for member in self.members.values():
            names = [_norm_name(n) for n in (member.nick, member.display_name, member.username) if n]
            if wanted in names:
                scored.append((0, member))
            elif any(n.startswith(wanted) for n in names) or any(wanted in n.split() for n in names):
                scored.append((1, member))
        if scored:
            scored.sort(key=lambda s: (s[0], not s[1].in_guild, s[1].is_bot))
            best = [m for rank, m in scored if rank == scored[0][0]]
            if len(best) > 1 and scored[0][0] == 1:
                options = ", ".join(f"@{m.username}" for m in best[:5])
                raise QueryError(f"Ambiguous person {ref}: {options}. Use the exact username.")
            return best[0]
        all_names = {m.id: _norm_name(m.name) for m in self.members.values()}
        all_names.update({-m.id: _norm_name(m.username) for m in self.members.values()})
        matches = process.extract(wanted, all_names, scorer=fuzz.WRatio, limit=3, score_cutoff=70)
        hints = sorted({f"@{self.members[abs(int(k))].username}" for _, _, k in matches})
        suffix = f" Did you mean {', '.join(hints)}?" if hints else ""
        raise QueryError(f"Unknown person {ref}.{suffix}")
