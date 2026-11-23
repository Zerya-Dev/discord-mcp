from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from zerya_discord_mcp.index.vocab import Vocabulary
from zerya_discord_mcp.query.search import SearchEngine
from zerya_discord_mcp.testing.fixtures import GUILD_ID, IndexBuilder

TZ = ZoneInfo("Europe/Warsaw")


@dataclass
class Server:
    builder: IndexBuilder
    grafika: int
    general: int
    admin: int
    ola: int
    wiktor: int
    norbert: int

    def search(self, q: str, allowed: frozenset[int] | None = None, **kwargs: object) -> str:
        engine = SearchEngine(Vocabulary(), TZ, GUILD_ID)
        with self.builder.db.snapshot() as conn:
            return engine.search(conn, allowed or self.builder.all_channels(), q, **kwargs).text  # pyrefly: ignore


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    b = IndexBuilder(tmp_path / "index.sqlite3")
    grafika = b.channel("fundacja-grafika")
    general = b.channel("general")
    admin = b.channel("admin-secret")
    ola = b.member(1, "ola", nick="Ola")
    wiktor = b.member(2, "wikipop", nick="Wiktor")
    norbert = b.member(3, "norbiros", nick="Norbert")
    b.say(general, ola, "hej, ktoś widział nowe lego?")
    b.say(grafika, ola, "wrzucam 3 propozycje logo dla wikipopa")
    b.say(grafika, wiktor, "fajne")
    b.say(grafika, norbert, "fundacja chce wersję mono do pism")
    b.say(grafika, ola, "ok zrobię jutro")
    for i in range(20):
        b.say(general, wiktor, f"random gadka {i}")
    b.say(general, norbert, "logotyp fundacji będzie w src/assets/logo.svg")
    b.say(admin, wiktor, "tajny plan: logo zarządu i budżet fundacji")
    yield Server(b, grafika, general, admin, ola, wiktor, norbert)
    b.db.close()
