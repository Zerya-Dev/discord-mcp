import re
import string
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from zerya_discord_mcp.access.oauth import OAuthStore
from zerya_discord_mcp.commands import COLOR_ERROR, COLOR_OK, COLOR_WARN, CommandResult, McpCommands
from zerya_discord_mcp.config import Config
from zerya_discord_mcp.diagnostics import SetupCheck
from zerya_discord_mcp.i18n import _TEXTS, from_accept_language, from_locale, t
from zerya_discord_mcp.index.db import Database
from zerya_discord_mcp.testing.fixtures import GUILD_ID
from zerya_discord_mcp.tests.grants import issue_grant

USER, STRANGER = 42, 43
CODE_BLOCK = re.compile(r"^```\n([A-Z2-9]{4}-[A-Z2-9]{4})\n```$")
RELATIVE = r"<t:\d+:R>"


class Env:
    def __init__(self, tmp_path: Path, **config: Any) -> None:
        self.db = Database(tmp_path / "c.sqlite3")
        self.db.open()
        self.store = OAuthStore(self.db, access_ttl=3600, refresh_ttl=86400, login_ttl=600)
        self.config = Config(db_path=tmp_path / "c.sqlite3", guild_id=GUILD_ID, **config)
        self.checks: list[SetupCheck] = []
        self.commands = McpCommands(self.store, self.config, lambda uid: uid == USER, self._report)

    async def _report(self) -> list[SetupCheck]:
        return self.checks


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path, public_url="https://mcp.example")
    yield e
    e.db.close()


async def test_login_in_english(env: Env) -> None:
    result = await env.commands.login(USER, "en-US")
    assert result.title == "🔐 MCP sign-in code"
    match = CODE_BLOCK.match(result.description)
    assert match is not None
    fields = dict(result.fields)
    assert re.fullmatch(rf"{RELATIVE} · single use", fields["Valid"])
    assert "`https://mcp.example/mcp`" in fields["Not connected yet?"]
    assert fields["What the agent sees"].endswith("The agent can read on your behalf.")
    assert result.ephemeral and result.footer is not None and "Never share this code" in result.footer
    assert env.store.consume_login_code(match.group(1)) == USER

    embed = result.to_embed()
    assert embed.description == result.description
    assert [f.name for f in embed.fields] == list(fields)
    assert embed.footer is not None and embed.footer.text == result.footer


async def test_login_in_polish_with_posting(tmp_path: Path) -> None:
    env = Env(tmp_path, write_enabled=True)
    result = await env.commands.login(USER, "pl")
    assert result.title == "🔐 Kod logowania MCP"
    assert CODE_BLOCK.match(result.description)
    fields = dict(result.fields)
    assert re.fullmatch(rf"{RELATIVE} · jednorazowy", fields["Ważny"])
    assert "czytać i pisać" in fields["Co zobaczy agent"]
    assert "Jeszcze nie podłączony?" not in fields  # no public URL configured
    env.db.close()


async def test_login_requires_membership(env: Env) -> None:
    result = await env.commands.login(STRANGER, "pl")
    assert (result.title, result.color) == ("Brak dostępu", COLOR_ERROR)
    assert "```" not in result.description
    assert env.db.reader().execute("SELECT count(*) FROM login_codes").fetchone() == (0,)


async def test_list_shows_active_connections(env: Env) -> None:
    empty = await env.commands.list(USER)
    assert empty.description == "You have no connected clients. Connect Claude with `/mcp login`."
    assert (await env.commands.list(USER, "pl")).description.startswith("Nie masz podłączonych klientów")

    reader, _ = issue_grant(env.store, USER, "read")
    writer, _ = issue_grant(env.store, USER, "read", "write")
    revoked, _ = issue_grant(env.store, USER)
    env.store.revoke_grant(revoked)

    english = await env.commands.list(USER)
    fields = dict(english.fields)
    assert set(fields) == {f"Claude · `{reader}`", f"Claude · `{writer}`"}
    assert re.fullmatch(rf"read only · connected {RELATIVE} · last used {RELATIVE}", fields[f"Claude · `{reader}`"])
    assert fields[f"Claude · `{writer}`"].startswith("reads and posts")
    assert english.footer == "Disconnect: /mcp revoke <id> or /mcp revoke all"

    polish = dict((await env.commands.list(USER, "pl")).fields)
    assert polish[f"Claude · `{reader}`"].startswith("tylko czyta · podłączony <t:")
    assert polish[f"Claude · `{writer}`"].startswith("czyta i pisze")
    assert (await env.commands.list(STRANGER)).fields == []


async def test_revoke(env: Env) -> None:
    first, _ = issue_grant(env.store, USER)
    issue_grant(env.store, USER)
    issue_grant(env.store, USER)
    other, _ = issue_grant(env.store, STRANGER)

    missing = await env.commands.revoke(USER, "  ")
    assert (missing.title, missing.color) == ("Nothing to disconnect", COLOR_ERROR)
    none = await env.commands.revoke(USER, other, "pl")  # someone else's connection
    assert none.description == f"Brak aktywnego połączenia `{other}`. Sprawdź `/mcp list`."

    one = await env.commands.revoke(USER, first)
    assert (one.title, one.color) == ("✅ Disconnected", COLOR_OK)
    assert one.description.startswith("Disconnected 1 client(s).")
    rest = await env.commands.revoke(USER, "ALL", "pl")
    assert rest.description.startswith("Odłączono klientów: 2.")
    assert [g.active for g in env.store.list_grants(STRANGER)] == [True]


async def test_setup_marks_failed_and_optional_checks(env: Env) -> None:
    env.checks = [
        SetupCheck(True, "Bot is on the server", "Zerya"),
        SetupCheck(False, "Permission: View Channels", "grant it"),
        SetupCheck(False, "Public URL", "must be HTTPS", required=False),
    ]
    result = await env.commands.setup()
    assert result.color == COLOR_ERROR
    assert result.description.splitlines() == [
        "Fix the items marked ❌ (⚠️ are optional).",
        "",
        "✅ **Bot is on the server** — Zerya",
        "❌ **Permission: View Channels** — grant it",
        "⚠️ **Public URL** — must be HTTPS",
    ]

    env.checks = [SetupCheck(True, "Bot is on the server"), SetupCheck(False, "Public URL", required=False)]
    warned = await env.commands.setup("pl")
    assert warned.color == COLOR_WARN
    assert warned.description.startswith("Popraw pozycje oznaczone ❌")
    assert warned.title == "🛠️ Konfiguracja MCP · Discord"

    env.checks = [SetupCheck(True, "Bot is on the server")]
    ready = await env.commands.setup()
    assert (ready.color, ready.description) == (COLOR_OK, "Everything is ready.\n\n✅ **Bot is on the server**")


def test_plain_text_content() -> None:
    text = CommandResult("Title", "Body", [("Name", "Value")], footer="Foot").content
    assert text == "**Title**\nBody\n**Name**\nValue\n-# Foot"


@pytest.mark.parametrize(
    ("locale", "expected"), [("pl", "pl"), ("PL", "pl"), ("en-US", "en"), ("en-GB", "en"), ("de", "en"), (None, "en")]
)
def test_from_locale(locale: str | None, expected: str) -> None:
    assert from_locale(locale) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7", "pl"),
        ("en-US,en;q=0.9,pl;q=0.8", "en"),
        ("de-DE,de;q=0.9,pl;q=0.5", "pl"),
        ("en;q=0.5, pl;q=0.8", "pl"),
        ("pl;q=abc, en;q=0.1", "en"),
        ("pl;q=0, de", "en"),  # q=0: explicitly not acceptable
        ("de, fr", "en"),
        ("*", "en"),
        ("", "en"),
        (None, "en"),
    ],
)
def test_from_accept_language(header: str | None, expected: str) -> None:
    assert from_accept_language(header) == expected


def test_every_text_exists_in_both_languages_with_the_same_placeholders() -> None:
    def fields(text: str) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}

    for key, texts in _TEXTS.items():
        assert set(texts) == {"en", "pl"}, key
        assert fields(texts["en"]) == fields(texts["pl"]), key
    assert t("revoke.done", "pl", count=3) == "Odłączono klientów: 3. Ich tokeny przestały działać od razu."
