"""hikari objects → index records. Display text is computed once here, so reads only copy strings."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import hikari

from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord, MemberRecord, MessageRecord, RoleRecord

# DEFAULT, REPLY, CHAT_INPUT_COMMAND (missing from hikari's enum), CONTEXT_MENU_COMMAND.
INDEXED_TYPES = frozenset({0, 19, 20, 23})

_USER_RE = re.compile(r"<@!?(\d+)>")
_ROLE_RE = re.compile(r"<@&(\d+)>")
_EMOJI_RE = re.compile(r"<a?:(\w+):\d+>")
_TIME_RE = re.compile(r"<t:(-?\d+)(?::[tTdDfFR])?>")
_LINK_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True, slots=True)
class Resolvers:
    user: Callable[[int], str | None]
    role: Callable[[int], str | None]


def resolve_markup(text: str, resolvers: Resolvers, users: Mapping[int, str]) -> str:
    def user(match: re.Match[str]) -> str:
        uid = int(match.group(1))
        name = users.get(uid) or resolvers.user(uid)
        return f"@{name}" if name else "@unknown-user"

    def role(match: re.Match[str]) -> str:
        name = resolvers.role(int(match.group(1)))
        return f"@{name}" if name else "@unknown-role"

    def timestamp(match: re.Match[str]) -> str:
        return datetime.fromtimestamp(int(match.group(1)), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    text = _ROLE_RE.sub(role, text)
    text = _USER_RE.sub(user, text)
    # Channel mentions stay as <#id>: they are resolved when rendering, against what the reader may see.
    text = _EMOJI_RE.sub(r":\1:", text)
    return _TIME_RE.sub(timestamp, text)


def _embed_text(embeds: Sequence[hikari.Embed]) -> list[str]:
    out: list[str] = []
    for embed in embeds:
        parts = [p for p in (embed.title, embed.description) if p]
        parts += [f"{f.name}: {f.value}" for f in embed.fields]
        if parts:
            out.append("[embed: " + " — ".join(parts)[:800] + "]")
    return out


def _attachments(attachments: Sequence[hikari.Attachment]) -> list[str]:
    return [f"[file: {a.filename}]" for a in attachments]


def _defined[T](value: hikari.UndefinedNoneOr[T]) -> T | None:
    return None if value is hikari.UNDEFINED or value is None else value


def display_text(message: hikari.PartialMessage, resolvers: Resolvers) -> str:
    users: dict[int, str] = {}
    mentions = _defined(message.user_mentions)
    if mentions:
        users = {int(uid): u.global_name or u.username for uid, u in mentions.items()}
    parts: list[str] = []
    content = _defined(message.content)
    if content:
        parts.append(content)
    author = _defined(message.author)
    embeds = _defined(message.embeds) or ()
    # Link previews are noise; embeds sent by bots and webhooks (GitHub, CI, Zeryon) are content.
    if embeds and (not content or (author is not None and author.is_bot) or _defined(message.webhook_id)):
        parts += _embed_text(embeds)
    parts += _attachments(_defined(message.attachments) or ())
    parts += [f"[sticker: {s.name}]" for s in _defined(message.stickers) or ()]
    poll = _defined(message.poll)
    if poll is not None:
        answers = " / ".join(a.poll_media.text or "" for a in poll.answers)
        parts.append(f"[poll: {poll.question.text or ''} — {answers}]")
    for snapshot in message.message_snapshots or ():
        forwarded = [snapshot.content or ""] + _attachments(snapshot.attachments)
        parts.append("[forwarded: " + " ".join(p for p in forwarded if p) + "]")
    return resolve_markup("\n".join(parts), resolvers, users).strip()


def message_record(message: hikari.PartialMessage, resolvers: Resolvers) -> MessageRecord | None:
    """None for messages without searchable text (system messages, empty messages)."""
    kind = _defined(message.type)
    if kind is not None and int(kind) not in INDEXED_TYPES:
        return None
    author = _defined(message.author)
    if author is None:
        return None
    text = display_text(message, resolvers)
    if not text:
        return None
    reference = _defined(message.message_reference)
    edited = _defined(message.edited_timestamp)
    mentions = _defined(message.user_mentions) or {}
    reactions = _defined(message.reactions) or ()
    return MessageRecord(
        id=int(message.id),
        channel_id=int(message.channel_id),
        author_id=int(author.id),
        content=text,
        reply_to_id=int(reference.id) if reference is not None and reference.id is not None else None,
        edited_at=int(edited.timestamp()) if edited is not None else None,
        mention_ids=tuple(int(uid) for uid in mentions),
        has_link=bool(_LINK_RE.search(text)),
        has_file=bool(_defined(message.attachments)),
        has_code="`" in (_defined(message.content) or ""),
        reactions=sum(r.count for r in reactions),
    )


def author_record(message: hikari.PartialMessage) -> MemberRecord | None:
    author = _defined(message.author)
    if author is None:
        return None
    member = _defined(message.member)
    return MemberRecord(
        id=int(author.id),
        username=author.username,
        display_name=author.global_name,
        nick=member.nickname if member is not None else None,
        is_bot=author.is_bot,
    )


def member_record(member: hikari.Member) -> MemberRecord:
    return MemberRecord(
        id=int(member.id),
        username=member.username,
        display_name=member.user.global_name,
        nick=member.nickname,
        role_ids=tuple(int(r) for r in member.role_ids if r != member.guild_id),
        is_bot=member.is_bot,
    )


def role_record(role: hikari.Role) -> RoleRecord:
    return RoleRecord(id=int(role.id), name=role.name, position=role.position)


def _tag_names(thread: hikari.GuildThreadChannel, forum: hikari.GuildChannel | None) -> tuple[str, ...]:
    if not isinstance(thread, hikari.GuildPublicThread) or not isinstance(forum, hikari.GuildForumChannel):
        return ()
    names = {int(t.id): t.name for t in forum.available_tags}
    return tuple(names[int(t)] for t in thread.applied_tag_ids if int(t) in names)


def channel_record(
    channel: hikari.GuildChannel, parent_lookup: Callable[[int], hikari.GuildChannel | None]
) -> ChannelRecord:
    parent_id = int(channel.parent_id) if channel.parent_id is not None else None
    if isinstance(channel, hikari.GuildThreadChannel):
        parent = parent_lookup(parent_id) if parent_id is not None else None
        is_forum = isinstance(parent, hikari.GuildForumChannel | hikari.GuildMediaChannel)
        return ChannelRecord(
            id=int(channel.id),
            name=channel.name or str(channel.id),
            kind=ChannelKind.FORUM_POST if is_forum else ChannelKind.THREAD,
            parent_id=parent_id,
            tags=_tag_names(channel, parent),
            is_private=isinstance(channel, hikari.GuildPrivateThread),
            owner_id=int(channel.owner_id),
            archived=channel.metadata.is_archived,
        )
    kind = ChannelKind.OTHER
    topic: str | None = None
    tags: tuple[str, ...] = ()
    match channel:
        case hikari.GuildNewsChannel():
            kind, topic = ChannelKind.NEWS, channel.topic
        case hikari.GuildTextChannel():
            kind, topic = ChannelKind.TEXT, channel.topic
        case hikari.GuildForumChannel() | hikari.GuildMediaChannel():
            kind, topic = ChannelKind.FORUM, channel.topic
            tags = tuple(t.name for t in channel.available_tags)
        case hikari.GuildCategory():
            kind = ChannelKind.CATEGORY
        case hikari.GuildVoiceChannel() | hikari.GuildStageChannel():
            kind = ChannelKind.VOICE
        case _:
            pass
    return ChannelRecord(
        id=int(channel.id),
        name=channel.name or str(channel.id),
        kind=kind,
        parent_id=parent_id,
        topic=topic,
        tags=tags,
        position=channel.position if isinstance(channel, hikari.PermissibleGuildChannel) else 0,
    )


def is_message_channel(record: ChannelRecord) -> bool:
    return record.kind in (ChannelKind.TEXT, ChannelKind.NEWS, ChannelKind.VOICE) or record.kind.is_thread
