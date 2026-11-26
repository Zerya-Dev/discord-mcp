import asyncio
import base64
import hashlib
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import hikari
import httpx2 as httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from zerya_discord_mcp import REQUIRED_INTENTS, Config, ZeryaDiscordMcp
from zerya_discord_mcp.index import writer
from zerya_discord_mcp.index.models import ChannelKind, ChannelRecord, MessageRecord
from zerya_discord_mcp.mcp import McpAsgiApp
from zerya_discord_mcp.mcp._oauth import OAuthStore
from zerya_discord_mcp.testing.fixtures import GUILD_ID

USER = 42
ORIGIN = "http://localhost"
REDIRECT = "http://localhost:9999/callback"


def _core(tmp_path: Path, **config: Any) -> ZeryaDiscordMcp:
    intents = hikari.Intents.ALL_UNPRIVILEGED | REQUIRED_INTENTS
    bot = hikari.GatewayBot(token="MTIz.fake.token", intents=intents, banner=None)
    settings: dict[str, Any] = {"public_url": ORIGIN} | config
    core = ZeryaDiscordMcp(bot, Config(db_path=tmp_path / "s.sqlite3", guild_id=GUILD_ID, **settings))
    core.db.open()
    core.db.write(lambda c: writer.upsert_channels(c, [ChannelRecord(id=7, name="general", kind=ChannelKind.TEXT)]))
    core.db.write(
        lambda c: writer.upsert_messages(
            c, [MessageRecord(id=1 << 40, channel_id=7, author_id=USER, content="ustalamy logo fundacji")]
        )
    )
    core._loop = asyncio.get_running_loop()  # start() without a gateway

    async def allowed(user_id: int) -> frozenset[int] | None:
        return frozenset({7}) if user_id == USER else None

    core.allowed_channels = allowed
    return core


def _tokens(core: ZeryaDiscordMcp) -> OAuthStore:
    config = core.config
    return OAuthStore(
        core.db,
        core.grants,
        access_ttl=config.access_token_ttl_seconds,
        refresh_ttl=config.refresh_token_ttl_days * 86400,
    )


def _client(core: ZeryaDiscordMcp, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=McpAsgiApp(core)), base_url=ORIGIN, headers=headers)


async def _sign_in(core: ZeryaDiscordMcp, http: httpx.AsyncClient) -> dict[str, str]:
    """Runs the whole OAuth flow the way claude.ai does and returns the token response."""
    registration = await http.post(
        "/register",
        json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none", "client_name": "Claude"},
    )
    assert registration.status_code == 201, registration.text
    client_id = registration.json()["client_id"]

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    authorize = await http.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
    )
    assert authorize.status_code == 302
    login_url = authorize.headers["location"]
    assert login_url.startswith(f"{ORIGIN}/login?request=")
    request_id = parse_qs(urlparse(login_url).query)["request"][0]

    page = await http.get(login_url)
    assert page.status_code == 200 and "Claude" in page.text and "/mcp login" in page.text
    assert page.headers["x-frame-options"] == "DENY"

    wrong = await http.post("/login", data={"request": request_id, "code": "AAAA-AAAA"})
    assert wrong.status_code == 400 and "Attempts left: 4" in wrong.text

    code, _ = core.grants.create_login_code(USER)
    approved = await http.post("/login", data={"request": request_id, "code": code.lower()})
    assert approved.status_code == 302
    callback = urlparse(approved.headers["location"])
    params = parse_qs(callback.query)
    assert f"{callback.scheme}://{callback.netloc}{callback.path}" == REDIRECT
    assert params["state"] == ["xyz"]
    issuer = (await http.get("/.well-known/oauth-authorization-server")).json()["issuer"]
    assert params["iss"] == [issuer]  # RFC 9207: exactly the published issuer
    assert core.grants.consume_login_code(code) is None  # single use

    token = await http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": params["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.text
    body = dict(token.json())
    body["client_id"] = client_id
    return body


async def test_discovery_and_unauthenticated_requests(tmp_path: Path) -> None:
    async with _client(_core(tmp_path)) as http:
        response = await http.post("/mcp", json={})
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert f'resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource/mcp"' in challenge
        resource = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert resource["resource"] == f"{ORIGIN}/mcp"
        assert [s.rstrip("/") for s in resource["authorization_servers"]] == [ORIGIN]
        server = (await http.get("/.well-known/oauth-authorization-server")).json()
        assert server["registration_endpoint"] == f"{ORIGIN}/register"
        assert server["code_challenge_methods_supported"] == ["S256"]


async def test_oauth_flow_then_tool_call(tmp_path: Path) -> None:
    core = _core(tmp_path)
    async with _client(core) as http:
        tokens = await _sign_in(core, http)
    assert tokens["scope"] == "read"
    client = _client(core, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    async with streamable_http_client(f"{ORIGIN}/mcp", http_client=client) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            assert {t.name for t in (await session.list_tools()).tools} == {"search", "read", "info"}
            result = await session.call_tool("search", {"q": "fundacja logo"})
            text = result.content[0].text  # pyrefly: ignore[missing-attribute]
            assert "ustalamy logo fundacji" in text
    grant = core.grants.list_grants(USER)[0]
    audit = core.db.reader().execute("SELECT tool, user_id, grant_id, result_count FROM audit_log").fetchall()
    assert audit == [("search", USER, grant.id, 1)]
    assert "Claude" in (await core.commands.list(USER)).content


async def test_refresh_rotation_and_reuse_detection(tmp_path: Path) -> None:
    core = _core(tmp_path)
    async with _client(core) as http:
        tokens = await _sign_in(core, http)

        async def refresh(token: str) -> httpx.Response:
            return await http.post(
                "/token",
                data={"grant_type": "refresh_token", "refresh_token": token, "client_id": tokens["client_id"]},
            )

        rotated = await refresh(tokens["refresh_token"])
        assert rotated.status_code == 200
        new_access = rotated.json()["access_token"]
        assert _tokens(core).verify_access(new_access) is not None

        # Replaying the old refresh token means it leaked: the whole connection ends.
        assert (await refresh(tokens["refresh_token"])).status_code == 400
        assert _tokens(core).verify_access(new_access) is None
        assert (await refresh(rotated.json()["refresh_token"])).status_code == 400


async def test_revoke_from_discord_cuts_access(tmp_path: Path) -> None:
    core = _core(tmp_path)
    async with _client(core) as http:
        tokens = await _sign_in(core, http)
        assert _tokens(core).verify_access(tokens["access_token"]) is not None
        assert "Disconnected 1" in (await core.commands.revoke(USER, "all")).content
        response = await http.post("/mcp", json={}, headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert response.status_code == 401


async def test_too_many_wrong_codes_drop_the_request(tmp_path: Path) -> None:
    core = _core(tmp_path)
    async with _client(core) as http:
        registration = await http.post(
            "/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"}
        )
        client_id = registration.json()["client_id"]
        authorize = await http.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT,
                "code_challenge": "x" * 43,
                "code_challenge_method": "S256",
            },
        )
        request_id = parse_qs(urlparse(authorize.headers["location"]).query)["request"][0]
        response = None
        for _ in range(5):
            response = await http.post("/login", data={"request": request_id, "code": "BADC-ODE2"})
        assert response is not None and "expired" in response.text
        code, _ = core.grants.create_login_code(USER)
        response = await http.post("/login", data={"request": request_id, "code": code})
        assert response.status_code == 400 and "expired" in response.text


async def _authorize(
    http: httpx.AsyncClient, redirect: str = REDIRECT, client_name: str = "Claude", scope: str | None = None
) -> httpx.Response:
    registration = await http.post(
        "/register",
        json={"redirect_uris": [redirect], "token_endpoint_auth_method": "none", "client_name": client_name},
    )
    assert registration.status_code == 201, registration.text
    params = {
        "response_type": "code",
        "client_id": registration.json()["client_id"],
        "redirect_uri": redirect,
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
        "state": "xyz",
    }
    if scope:
        params["scope"] = scope
    return await http.get("/authorize", params=params)


async def test_redirect_host_allowlist(tmp_path: Path) -> None:
    core = _core(tmp_path, oauth_redirect_hosts=frozenset({"claude.ai"}))
    async with _client(core) as http:
        refused = await _authorize(http)
        # The client hears why (an OAuth error on its redirect URI), but no sign-in starts and no code is sent.
        assert refused.status_code == 302
        error = parse_qs(urlparse(refused.headers["location"]).query)
        assert error["error"] == ["unauthorized_client"] and "code" not in error
        assert core.db.reader().execute("SELECT count(*) FROM oauth_requests").fetchone() == (0,)

        allowed = await _authorize(http, "https://claude.ai/api/mcp/auth_callback")
        assert allowed.status_code == 302
        assert allowed.headers["location"].startswith(f"{ORIGIN}/login?request=")


async def test_sign_in_page_shows_where_the_code_goes_in_the_browser_language(tmp_path: Path) -> None:
    core = _core(tmp_path, write_enabled=True, server_name="Zerya")
    async with _client(core) as http:
        authorize = await _authorize(http, "https://claude.ai/api/mcp/auth_callback", "<b>Claude</b>", "read write")
        login_url = authorize.headers["location"]

        english = await http.get(login_url)
        assert '<html lang="en">' in english.text
        assert "After connecting you will be sent to <strong>claude.ai</strong>." in english.text
        assert "&lt;b&gt;Claude&lt;/b&gt;" in english.text and "<b>Claude</b>" not in english.text
        assert "read and post messages in" in english.text

        polish = await http.get(login_url, headers={"Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8"})
        assert polish.headers["content-language"] == "pl"
        assert '<html lang="pl">' in polish.text
        assert "<h1>Połącz z serwerem Zerya</h1>" in polish.text
        assert "Po połączeniu wrócisz do <strong>claude.ai</strong>." in polish.text

        expired = await http.get("/login?request=nope", headers={"Accept-Language": "pl"})
        assert expired.status_code == 400 and "Logowanie wygasło" in expired.text


async def test_registration_is_rate_limited_per_ip(tmp_path: Path) -> None:
    core = _core(tmp_path)
    body = {"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"}
    async with _client(core) as http:
        statuses = [(await http.post("/register", json=body)).status_code for _ in range(31)]
        assert statuses == [201] * 30 + [429]
        limited = await http.post("/register", json=body)
        assert limited.headers["retry-after"] == "30"
        assert limited.json() == {"error": "slow_down"}
