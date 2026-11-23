"""Discord API payloads (shaped like the gateway and REST send them) turned into real hikari objects.

Ingest and access tests feed these to the code under test instead of hand-made fakes, so hikari's own parsing
(UNDEFINED vs None, empty content, member role lists) is part of what is tested.
"""

import itertools
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import Mock

import hikari
from hikari.impl.entity_factory import EntityFactoryImpl

from zerya_discord_mcp.index.models import snowflake_at
from zerya_discord_mcp.testing.fixtures import GUILD_ID

type Json = dict[str, Any]

TIMESTAMP = "2026-09-14T12:00:00.000000+00:00"
FACTORY = EntityFactoryImpl(cast(hikari.RESTAware, Mock()))
_ids = itertools.count(1)


def message_id(at: datetime, offset: int = 0) -> int:
    """A snowflake created at ``at`` (``offset`` keeps IDs unique within the same millisecond)."""
    return snowflake_at(at.astimezone(UTC)) + offset


# Users, members, roles ------------------------------------------------------------------------------------


def user(uid: int, username: str, *, global_name: str | None = None, bot: bool = False) -> Json:
    return {
        "id": str(uid),
        "username": username,
        "discriminator": "0",
        "global_name": global_name,
        "avatar": None,
        "bot": bot,
    }


def member_payload(
    uid: int,
    username: str,
    *,
    nick: str | None = None,
    global_name: str | None = None,
    roles: tuple[int, ...] = (),
    timed_out_until: datetime | None = None,
) -> Json:
    return {
        "user": user(uid, username, global_name=global_name),
        "nick": nick,
        "roles": [str(r) for r in roles],
        "joined_at": TIMESTAMP,
        "deaf": False,
        "mute": False,
        "communication_disabled_until": timed_out_until.isoformat() if timed_out_until else None,
    }


def member(
    uid: int,
    username: str,
    *,
    nick: str | None = None,
    global_name: str | None = None,
    roles: tuple[int, ...] = (),
    timed_out_until: datetime | None = None,
) -> hikari.Member:
    payload = member_payload(
        uid, username, nick=nick, global_name=global_name, roles=roles, timed_out_until=timed_out_until
    )
    return FACTORY.deserialize_member(payload, guild_id=hikari.Snowflake(GUILD_ID))


def role(rid: int, name: str, *, permissions: int = 0, position: int = 0) -> hikari.Role:
    payload: Json = {
        "id": str(rid),
        "name": name,
        "color": 0,
        "hoist": False,
        "position": position,
        "permissions": str(permissions),
        "managed": False,
        "mentionable": False,
    }
    return FACTORY.deserialize_role(payload, guild_id=hikari.Snowflake(GUILD_ID))


# Messages -------------------------------------------------------------------------------------------------


def message_payload(mid: int, channel_id: int, author: Json, content: str = "", **extra: Any) -> Json:
    payload: Json = {
        "id": str(mid),
        "channel_id": str(channel_id),
        "guild_id": str(GUILD_ID),
        "author": author,
        "content": content,
        "timestamp": TIMESTAMP,
        "edited_timestamp": None,
        "tts": False,
        "mention_everyone": False,
        "mentions": [],
        "mention_roles": [],
        "attachments": [],
        "embeds": [],
        "pinned": False,
        "type": 0,
        "flags": 0,
    }
    payload.update(extra)
    return payload


def message(mid: int, channel_id: int, author: Json, content: str = "", **extra: Any) -> hikari.Message:
    return FACTORY.deserialize_message(message_payload(mid, channel_id, author, content, **extra))


def attachment(filename: str) -> Json:
    aid = next(_ids)
    return {
        "id": str(aid),
        "filename": filename,
        "size": 1024,
        "url": f"https://cdn.discordapp.com/attachments/1/{aid}/{filename}",
        "proxy_url": f"https://media.discordapp.net/attachments/1/{aid}/{filename}",
    }


def embed(title: str | None = None, description: str | None = None, url: str | None = None) -> Json:
    payload: Json = {"type": "rich" if url is None else "link"}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if url is not None:
        payload["url"] = url
    return payload


# Channels -------------------------------------------------------------------------------------------------


def overwrite(target: int, *, is_member: bool = False, allow: int = 0, deny: int = 0) -> Json:
    return {"id": str(target), "type": 1 if is_member else 0, "allow": str(allow), "deny": str(deny)}


def _channel(
    cid: int, name: str, kind: hikari.ChannelType, parent: int | None, position: int, overwrites: list[Json]
) -> Json:
    return {
        "id": str(cid),
        "guild_id": str(GUILD_ID),
        "name": name,
        "type": int(kind),
        "parent_id": str(parent) if parent is not None else None,
        "position": position,
        "permission_overwrites": overwrites,
    }


def text_channel(
    cid: int,
    name: str,
    *,
    parent: int | None = None,
    topic: str | None = None,
    position: int = 0,
    news: bool = False,
    last_message_id: int | None = None,
    overwrites: list[Json] | None = None,
) -> Json:
    kind = hikari.ChannelType.GUILD_NEWS if news else hikari.ChannelType.GUILD_TEXT
    payload = _channel(cid, name, kind, parent, position, overwrites or [])
    payload["topic"] = topic
    payload["last_message_id"] = str(last_message_id) if last_message_id else None
    return payload


def category(cid: int, name: str, *, position: int = 0) -> Json:
    return _channel(cid, name, hikari.ChannelType.GUILD_CATEGORY, None, position, [])


def voice_channel(cid: int, name: str, *, parent: int | None = None) -> Json:
    payload = _channel(cid, name, hikari.ChannelType.GUILD_VOICE, parent, 0, [])
    payload.update(bitrate=64000, user_limit=0, rtc_region=None, video_quality_mode=1)
    return payload


def forum_channel(cid: int, name: str, *, tags: dict[int, str], topic: str | None = None) -> Json:
    payload = _channel(cid, name, hikari.ChannelType.GUILD_FORUM, None, 0, [])
    payload["topic"] = topic
    payload["available_tags"] = [
        {"id": str(tid), "name": tag, "moderated": False, "emoji_id": None, "emoji_name": None}
        for tid, tag in tags.items()
    ]
    return payload


def thread(
    cid: int,
    name: str,
    parent: int,
    owner: int,
    *,
    private: bool = False,
    archived: bool = False,
    tags: tuple[int, ...] = (),
    last_message_id: int | None = None,
) -> Json:
    kind = hikari.ChannelType.GUILD_PRIVATE_THREAD if private else hikari.ChannelType.GUILD_PUBLIC_THREAD
    return {
        "id": str(cid),
        "guild_id": str(GUILD_ID),
        "name": name,
        "type": int(kind),
        "parent_id": str(parent),
        "owner_id": str(owner),
        "member_count": 1,
        "message_count": 1,
        "applied_tags": [str(t) for t in tags],
        "last_message_id": str(last_message_id) if last_message_id else None,
        "thread_metadata": {
            "archived": archived,
            "archive_timestamp": TIMESTAMP,
            "locked": False,
            "auto_archive_duration": 1440,
        },
    }


def channel(payload: Json) -> hikari.GuildChannel:
    parsed = FACTORY.deserialize_channel(payload, guild_id=hikari.Snowflake(GUILD_ID))
    assert isinstance(parsed, hikari.GuildChannel)
    return parsed


def guild_thread(payload: Json) -> hikari.GuildThreadChannel:
    parsed = channel(payload)
    assert isinstance(parsed, hikari.GuildThreadChannel)
    return parsed


def thread_member(thread_id: int, user_id: int) -> hikari.ThreadMember:
    return FACTORY.deserialize_thread_member({
        "id": str(thread_id),
        "user_id": str(user_id),
        "join_timestamp": TIMESTAMP,
        "flags": 0,
    })


def forbidden() -> hikari.ForbiddenError:
    return hikari.ForbiddenError(
        url="https://discord.com/api/v10/test", headers={}, raw_body="", message="Missing Access"
    )
