"""Plain records stored in the index. No Discord library types here, so index/ and query/ test on SQLite alone."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

DISCORD_EPOCH_MS = 1_420_070_400_000


def snowflake_time(snowflake: int) -> datetime:
    return datetime.fromtimestamp(((snowflake >> 22) + DISCORD_EPOCH_MS) / 1000, tz=UTC)


def snowflake_at(moment: datetime) -> int:
    """Smallest snowflake created at ``moment`` (use as an inclusive lower bound)."""
    ms = int(moment.timestamp() * 1000) - DISCORD_EPOCH_MS
    return max(ms, 0) << 22


class ChannelKind(StrEnum):
    TEXT = "text"
    NEWS = "news"
    FORUM = "forum"
    CATEGORY = "category"
    VOICE = "voice"
    THREAD = "thread"
    FORUM_POST = "forum_post"
    OTHER = "other"

    @property
    def is_thread(self) -> bool:
        return self in (ChannelKind.THREAD, ChannelKind.FORUM_POST)


@dataclass(frozen=True, slots=True)
class MessageRecord:
    id: int
    channel_id: int
    author_id: int
    content: str
    reply_to_id: int | None = None
    edited_at: int | None = None
    mention_ids: tuple[int, ...] = ()
    has_link: bool = False
    has_file: bool = False
    has_code: bool = False
    reactions: int = 0


@dataclass(frozen=True, slots=True)
class ChannelRecord:
    id: int
    name: str
    kind: ChannelKind
    parent_id: int | None = None
    topic: str | None = None
    tags: tuple[str, ...] = ()
    position: int = 0
    is_private: bool = False
    owner_id: int | None = None
    archived: bool = False


@dataclass(frozen=True, slots=True)
class MemberRecord:
    id: int
    username: str
    display_name: str | None = None
    nick: str | None = None
    role_ids: tuple[int, ...] = field(default=())
    is_bot: bool = False
    in_guild: bool = True

    @property
    def name(self) -> str:
        return self.nick or self.display_name or self.username


@dataclass(frozen=True, slots=True)
class RoleRecord:
    id: int
    name: str
    position: int = 0
