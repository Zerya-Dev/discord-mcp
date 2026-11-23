from typing import Any

import hikari
import pytest

from zerya_discord_mcp.index.models import ChannelKind, MessageRecord
from zerya_discord_mcp.ingest.normalize import (
    Resolvers,
    author_record,
    channel_record,
    is_message_channel,
    member_record,
    message_record,
)
from zerya_discord_mcp.testing.fixtures import GUILD_ID
from zerya_discord_mcp.tests import payloads as p

CHANNEL = 7
OLA = p.user(1, "ola", global_name="Ola")
BOT = p.user(900, "zeryon", bot=True)
RESOLVERS = Resolvers(user={2: "Wiktor"}.get, role={50: "Zarząd"}.get)


def _record(content: str = "", *, author: p.Json = OLA, **extra: Any) -> MessageRecord | None:
    return message_record(p.message(1 << 30, CHANNEL, author, content, **extra), RESOLVERS)


def _text(content: str = "", **extra: Any) -> str:
    record = _record(content, **extra)
    assert record is not None
    return record.content


def test_mentions_emoji_and_timestamps_become_readable_text() -> None:
    text = _text(
        "<@1> <@!2> <@3> ping <@&50> <@&51> in <#7> <:pepe:123> <a:dance:456> at <t:1789387200:R> or <t:1789387200>",
        mentions=[OLA],
    )
    assert text == (
        "@Ola @Wiktor @unknown-user ping @Zarząd @unknown-role in <#7> :pepe: :dance: "
        "at 2026-09-14 12:00 UTC or 2026-09-14 12:00 UTC"
    )


def test_mention_ids_come_from_the_message() -> None:
    record = _record("<@1> hi", mentions=[OLA, p.user(2, "wikipop")])
    assert record is not None and set(record.mention_ids) == {1, 2}


def test_attachments_stickers_poll_and_forwarded_messages() -> None:
    poll = {
        "question": {"text": "Który termin?"},
        "answers": [
            {"answer_id": 1, "poll_media": {"text": "piątek"}},
            {"answer_id": 2, "poll_media": {"text": "sobota"}},
        ],
        "expiry": None,
        "allow_multiselect": False,
        "layout_type": 1,
    }
    record = _record(
        "zobaczcie",
        attachments=[p.attachment("logo.svg")],
        sticker_items=[{"id": "9", "name": "wave", "format_type": 1}],
        poll=poll,
        message_snapshots=[
            {"message": {"type": 0, "content": "stara wiadomość", "attachments": [p.attachment("a.pdf")]}}
        ],
    )
    assert record is not None
    assert record.content.splitlines() == [
        "zobaczcie",
        "[file: logo.svg]",
        "[sticker: wave]",
        "[poll: Który termin? — piątek / sobota]",
        "[forwarded: stara wiadomość [file: a.pdf]]",
    ]
    assert record.has_file


def test_forwarded_message_alone_is_indexed() -> None:
    text = _text(message_snapshots=[{"message": {"type": 0, "content": "<@2> napisał to", "attachments": []}}])
    assert text == "[forwarded: @Wiktor napisał to]"


def test_bot_embeds_are_content_but_human_link_previews_are_not() -> None:
    preview = p.embed("GitHub repo", "A repository", url="https://github.com/x/y")
    assert _text("https://github.com/x/y", embeds=[preview]) == "https://github.com/x/y"

    ci = p.embed("Build failed", "main · 3 tests")
    assert _text("CI", author=BOT, embeds=[ci]) == "CI\n[embed: Build failed — main · 3 tests]"
    webhook = _text("deploy", embeds=[ci], webhook_id="77")
    assert "[embed: Build failed" in webhook
    # A human message made only of an embed still has its text indexed.
    assert _text(embeds=[ci]) == "[embed: Build failed — main · 3 tests]"


@pytest.mark.parametrize("kind", [6, 7, 8, 18, 21])  # pin, member join, boost, thread created, starter message
def test_system_messages_are_skipped(kind: int) -> None:
    assert _record("Ola pinned a message", type=kind) is None


@pytest.mark.parametrize("kind", [0, 19, 20, 23])
def test_user_message_types_are_indexed(kind: int) -> None:
    assert _record("tekst", type=kind) is not None


def test_empty_messages_are_skipped() -> None:
    assert _record("") is None
    assert _record("   ") is None


def test_flags_reactions_reply_and_edit() -> None:
    record = _record(
        "patrz `npm run build` na https://example.com",
        reactions=[
            {"count": 3, "me": False, "emoji": {"id": None, "name": "👍"}},
            {"count": 2, "me": True, "emoji": {"id": "5", "name": "pepe"}},
        ],
        message_reference={"message_id": "123", "channel_id": str(CHANNEL)},
        edited_timestamp="2026-09-14T12:05:00.000000+00:00",
    )
    assert record is not None
    assert (record.has_link, record.has_file, record.has_code) == (True, False, True)
    assert record.reactions == 5
    assert record.reply_to_id == 123
    assert record.edited_at == 1789387200 + 300
    plain = _record("zwykły tekst")
    assert plain is not None
    assert (plain.has_link, plain.has_file, plain.has_code, plain.reactions, plain.reply_to_id) == (
        False,
        False,
        False,
        0,
        None,
    )


def test_author_and_member_records() -> None:
    message = p.message(1, CHANNEL, OLA, "x", member=p.member_payload(1, "ola", nick="Oleńka") | {"user": OLA})
    author = author_record(message)
    assert author is not None
    assert (author.username, author.display_name, author.nick, author.is_bot) == ("ola", "Ola", "Oleńka", False)

    record = member_record(p.member(2, "wikipop", nick="Wiktor", global_name="Wik", roles=(50, 51)))
    assert record.name == "Wiktor"
    assert record.display_name == "Wik"
    assert record.role_ids == (50, 51)  # without the implicit @everyone role (guild ID)
    assert GUILD_ID not in record.role_ids


def _lookup(*payloads: p.Json) -> dict[int, hikari.GuildChannel]:
    return {int(c.id): c for c in map(p.channel, payloads)}


def test_channel_records_for_every_kind() -> None:
    channels = _lookup(
        p.category(1, "Fundacja", position=2),
        p.text_channel(10, "general", parent=1, topic="o wszystkim", position=3),
        p.text_channel(11, "ogloszenia", news=True, topic="news"),
        p.voice_channel(12, "Pogadanki", parent=1),
        p.forum_channel(13, "pomysly", tags={100: "bug", 101: "idea", 102: "done"}, topic="forum"),
    )
    parent = channels.get

    category = channel_record(channels[1], parent)
    assert (category.kind, category.position, category.parent_id) == (ChannelKind.CATEGORY, 2, None)
    text = channel_record(channels[10], parent)
    assert (text.kind, text.topic, text.parent_id, text.position) == (ChannelKind.TEXT, "o wszystkim", 1, 3)
    news = channel_record(channels[11], parent)
    assert (news.kind, news.topic) == (ChannelKind.NEWS, "news")
    voice = channel_record(channels[12], parent)
    assert voice.kind is ChannelKind.VOICE
    forum = channel_record(channels[13], parent)
    assert (forum.kind, forum.tags, forum.topic) == (ChannelKind.FORUM, ("bug", "idea", "done"), "forum")

    assert [is_message_channel(r) for r in (category, text, news, voice, forum)] == [False, True, True, True, False]


def test_thread_records() -> None:
    channels = _lookup(
        p.text_channel(10, "general"),
        p.forum_channel(13, "pomysly", tags={100: "bug", 101: "idea"}),
    )
    parent = channels.get

    public = channel_record(p.guild_thread(p.thread(20, "wątek", 10, owner=1, archived=True)), parent)
    assert (public.kind, public.parent_id, public.is_private, public.owner_id, public.archived) == (
        ChannelKind.THREAD,
        10,
        False,
        1,
        True,
    )
    private = channel_record(p.guild_thread(p.thread(21, "tajny", 10, owner=2, private=True)), parent)
    assert (private.kind, private.is_private, private.owner_id) == (ChannelKind.THREAD, True, 2)

    post = channel_record(p.guild_thread(p.thread(22, "Nowe logo", 13, owner=1, tags=(101, 999))), parent)
    assert (post.kind, post.tags, post.name) == (ChannelKind.FORUM_POST, ("idea",), "Nowe logo")
    assert is_message_channel(post)

    # Parent not known (e.g. not cached yet): still a thread, just without tags.
    orphan = channel_record(p.guild_thread(p.thread(23, "post", 99, owner=1, tags=(101,))), parent)
    assert (orphan.kind, orphan.tags) == (ChannelKind.THREAD, ())
