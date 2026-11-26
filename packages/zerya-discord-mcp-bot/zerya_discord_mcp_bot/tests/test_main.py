import logging
from collections.abc import AsyncIterator
from pathlib import Path

import hikari
import httpx2 as httpx
import pytest

from zerya_discord_mcp import REQUIRED_INTENTS, Config, ZeryaDiscordMcp
from zerya_discord_mcp_bot.main import _RedactQuery, http_app

ORIGIN = "http://localhost"


def _access_record(*args: object) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None)


def test_access_log_drops_query_strings() -> None:
    record = _access_record(
        "10.0.0.1:5000", "GET", "/authorize?client_id=abc&code_challenge=secret&state=s", "1.1", 302
    )
    assert _RedactQuery().filter(record)
    assert record.args == ("10.0.0.1:5000", "GET", "/authorize?…", "1.1", 302)
    assert record.getMessage() == '10.0.0.1:5000 - "GET /authorize?… HTTP/1.1" 302'

    plain = _access_record("10.0.0.1:5000", "POST", "/mcp", "1.1", 200)
    assert _RedactQuery().filter(plain)
    assert plain.args == ("10.0.0.1:5000", "POST", "/mcp", "1.1", 200)


def test_records_without_tuple_args_pass_untouched() -> None:
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "started %(port)s", None, None)
    record.args = {"port": 8000}
    assert _RedactQuery().filter(record)
    assert record.args == {"port": 8000}


@pytest.fixture
async def zdm(tmp_path: Path) -> AsyncIterator[ZeryaDiscordMcp]:
    intents = hikari.Intents.ALL_UNPRIVILEGED | REQUIRED_INTENTS
    bot = hikari.GatewayBot(token="MTIz.fake.token", intents=intents, banner=None)  # never started: no network
    core = ZeryaDiscordMcp(bot, Config(db_path=tmp_path / "b.sqlite3", guild_id=1000, public_url=ORIGIN))
    yield core
    core.db.close()


async def test_health_is_503_until_started(zdm: ZeryaDiscordMcp) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=http_app(zdm)), base_url=ORIGIN) as http:
        response = await http.get("/health")
        assert response.status_code == 503
        assert response.headers["content-type"] == "application/json"
        body = response.json()
        assert body["ok"] is False
        assert (body["gateway"], body["database"]) == (False, False)
        assert body["backfill"] == {"done": False, "messages": 0, "failed_channels": 0}

        zdm.db.open()  # the database alone is not enough: the gateway is still down
        degraded = (await http.get("/health")).json()
        assert degraded["database"] is True and degraded["ok"] is False

        # The MCP app owns the rest of the origin.
        assert (await http.get("/.well-known/oauth-authorization-server")).status_code == 200
