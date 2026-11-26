import logging
from collections.abc import AsyncIterator
from pathlib import Path

import hikari
import pytest

from zerya_discord_mcp import REQUIRED_INTENTS, Config, SetupCheck, ZeryaDiscordMcp
from zerya_discord_mcp.tests.grants import add_grant


@pytest.fixture
async def zdm(tmp_path: Path) -> AsyncIterator[ZeryaDiscordMcp]:
    bot = hikari.GatewayBot(token="MTIz.fake.token", intents=hikari.Intents.ALL_UNPRIVILEGED | REQUIRED_INTENTS)
    core = ZeryaDiscordMcp(bot, Config(db_path=tmp_path / "zdm.sqlite3", guild_id=1))
    yield core
    if core.db.is_open:
        core.db.close()


async def test_starting_twice_is_refused(zdm: ZeryaDiscordMcp) -> None:
    """Regression: a host calling start() next to the arc plugin opened the database twice and ran two
    maintenance loops."""
    await zdm.start(backfill=False)
    with pytest.raises(RuntimeError, match="already started"):
        await zdm.start(backfill=False)
    await zdm.stop()

    await zdm.start(backfill=False)  # a stopped library can start again
    await zdm.stop()


async def test_start_refuses_missing_intents(tmp_path: Path) -> None:
    bot = hikari.GatewayBot(token="MTIz.fake.token", intents=hikari.Intents.ALL_UNPRIVILEGED)
    with pytest.warns(hikari.MissingIntentWarning):  # hikari already warns when the library subscribes
        core = ZeryaDiscordMcp(bot, Config(db_path=tmp_path / "zdm.sqlite3", guild_id=1))
    with pytest.raises(RuntimeError, match="needs these gateway intents"):
        await core.start(backfill=False)


async def test_housekeeping_runs_the_core_and_every_integration(zdm: ZeryaDiscordMcp) -> None:
    await zdm.start(backfill=False)
    grant = add_grant(zdm.grants, 42)
    zdm.grants.revoke_grant(grant)
    zdm.db.write(lambda c: c.execute("UPDATE grants SET revoked_at = 0 WHERE id = ?", (grant,)))
    runs: list[str] = []

    def prune() -> dict[str, int]:
        runs.append("integration")
        return {"integration_rows": 3}

    zdm.add_housekeeping(prune)

    zdm._housekeeping()

    assert runs == ["integration"]
    assert zdm.grants.list_grants(42) == []
    await zdm.stop()


async def test_setup_report_is_logged_with_problems_as_warnings(
    zdm: ZeryaDiscordMcp, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def report() -> list[SetupCheck]:
        return [SetupCheck(True, "Bot is on the server"), SetupCheck(False, "Public URL", required=False)]

    monkeypatch.setattr(zdm, "setup_report", report)
    with caplog.at_level(logging.INFO, logger="zerya_discord_mcp"):
        await zdm.log_setup_report()

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.INFO, "setup: [OK] Bot is on the server"),
        (logging.WARNING, "setup: [WARN] Public URL"),
    ]
