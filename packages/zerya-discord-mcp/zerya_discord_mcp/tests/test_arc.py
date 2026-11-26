from collections.abc import Iterator
from pathlib import Path

import arc
import hikari
import pytest

from zerya_discord_mcp import REQUIRED_INTENTS, Config, SetupCheck, ZeryaDiscordMcp
from zerya_discord_mcp.arc import McpPlugin


@pytest.fixture
def zdm(tmp_path: Path) -> Iterator[ZeryaDiscordMcp]:
    bot = hikari.GatewayBot(token="MTIz.fake.token", intents=hikari.Intents.ALL_UNPRIVILEGED | REQUIRED_INTENTS)
    yield ZeryaDiscordMcp(bot, Config(db_path=tmp_path / "zdm.sqlite3", guild_id=1))


class Calls:
    def __init__(self, zdm: ZeryaDiscordMcp, monkeypatch: pytest.MonkeyPatch, *, fail_start: bool = False) -> None:
        self.log: list[str] = []

        async def start() -> None:
            self.log.append("start")
            if fail_start:
                raise RuntimeError("database is locked")

        async def setup_report() -> list[SetupCheck]:
            self.log.append("setup_report")
            return []

        async def stop() -> None:
            self.log.append("stop")

        monkeypatch.setattr(zdm, "start", start)
        monkeypatch.setattr(zdm, "setup_report", setup_report)
        monkeypatch.setattr(zdm, "stop", stop)


async def _run_lifecycle(zdm: ZeryaDiscordMcp, plugin: McpPlugin) -> None:
    for callback in plugin.listeners.get(hikari.StartedEvent, ()):
        await callback(hikari.StartedEvent(app=zdm.bot))
    for callback in plugin.listeners.get(hikari.StoppingEvent, ()):
        await callback(hikari.StoppingEvent(app=zdm.bot))


def test_plugin_registers_mcp_and_mcp_setup(zdm: ZeryaDiscordMcp) -> None:
    client = arc.GatewayClient(zdm.bot)

    client.add_plugin(McpPlugin(zdm))

    assert {c.name for c in client.walk_commands(hikari.CommandType.SLASH)} == {
        "mcp",
        "login",
        "list",
        "revoke",
        "mcp-setup",
    }


async def test_plugin_starts_and_stops_the_library_with_the_bot(
    zdm: ZeryaDiscordMcp, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = Calls(zdm, monkeypatch)

    await _run_lifecycle(zdm, McpPlugin(zdm))

    assert calls.log == ["start", "setup_report", "stop"]


async def test_plugin_logs_a_failed_start_and_skips_the_checklist(
    zdm: ZeryaDiscordMcp, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = Calls(zdm, monkeypatch, fail_start=True)

    await _run_lifecycle(zdm, McpPlugin(zdm))

    assert calls.log == ["start", "stop"]
    assert "zerya-discord-mcp failed to start" in caplog.text


async def test_plugin_leaves_the_lifecycle_to_the_host_when_asked(
    zdm: ZeryaDiscordMcp, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = Calls(zdm, monkeypatch)
    plugin = McpPlugin(zdm, manage_lifecycle=False)

    await _run_lifecycle(zdm, plugin)

    assert calls.log == []
    assert hikari.StartedEvent not in plugin.listeners and hikari.StoppingEvent not in plugin.listeners
