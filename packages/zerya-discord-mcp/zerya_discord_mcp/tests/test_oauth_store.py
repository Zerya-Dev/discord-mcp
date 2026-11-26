import asyncio
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp.server.auth.provider import TokenError
from mcp.shared.auth import OAuthToken

from zerya_discord_mcp.access.grants import GrantStore
from zerya_discord_mcp.index.db import Database
from zerya_discord_mcp.mcp._oauth import OAuthStore
from zerya_discord_mcp.tests.grants import authorization_params, issue_grant, register_client

USER = 42
DAY = 86400


@pytest.fixture
def store(tmp_path: Path) -> Iterator[OAuthStore]:
    db = Database(tmp_path / "o.sqlite3")
    db.open()
    yield OAuthStore(db, GrantStore(db, login_ttl=600), access_ttl=3600, refresh_ttl=90 * DAY)
    db.close()


def _sql(store: OAuthStore, query: str, *params: object) -> None:
    store.db.write(lambda conn: conn.execute(query, params))


def _count(store: OAuthStore, table: str) -> int:
    return int(store.db.reader().execute(f"SELECT count(*) FROM {table}").fetchone()[0])


async def test_concurrent_refresh_with_the_same_token_succeeds_once(store: OAuthStore) -> None:
    grant_id, tokens = issue_grant(store, USER)
    assert tokens.refresh_token is not None
    client_id = store.grants.list_grants(USER)[0].client_id
    first = store.load_refresh(client_id, tokens.refresh_token)
    second = store.load_refresh(client_id, tokens.refresh_token)
    assert first is not None and second is not None  # both requests got past validation

    results = await asyncio.gather(
        asyncio.to_thread(store.exchange_refresh, first, []),
        asyncio.to_thread(store.exchange_refresh, second, []),
        return_exceptions=True,
    )

    issued = [r for r in results if isinstance(r, OAuthToken)]
    refused = [r for r in results if isinstance(r, TokenError)]
    assert len(issued) == 1 and len(refused) == 1
    assert refused[0].error == "invalid_grant"
    new = store.verify_access(issued[0].access_token)
    assert new is not None and new.id == grant_id


def test_housekeeping_prunes_expired_and_ended_state_but_keeps_active_grants(store: OAuthStore) -> None:
    now = int(time.time())
    month_and_a_day = now - 31 * DAY

    active, active_tokens = issue_grant(store, USER)
    recently_revoked, _ = issue_grant(store, USER)
    store.grants.revoke_grant(recently_revoked)
    long_revoked, _ = issue_grant(store, USER)
    store.grants.revoke_grant(long_revoked)
    _sql(store, "UPDATE grants SET revoked_at = ? WHERE id = ?", month_and_a_day, long_revoked)
    long_expired, _ = issue_grant(store, USER)
    _sql(store, "UPDATE grants SET expires_at = ? WHERE id = ?", month_and_a_day, long_expired)
    # The active grant's access token expired; its refresh token is still good.
    _sql(store, "UPDATE oauth_tokens SET expires_at = ? WHERE grant_id = ? AND kind = 'access'", now - 10, active)

    abandoned = register_client(store, "Abandoned")  # registered, never connected
    _sql(store, "UPDATE oauth_clients SET created_at = ? WHERE client_id = ?", now - 2 * DAY, abandoned.client_id)
    fresh = register_client(store, "Just registered")  # may still be signing in

    store.grants.create_login_code(USER)
    store.grants.create_login_code(USER + 1)
    _sql(store, "UPDATE login_codes SET expires_at = ? WHERE user_id = ?", now - 10, USER + 1)
    pending = store.get_request(store.create_request(fresh, authorization_params("read")))
    assert pending is not None
    store.complete_request(pending, USER, ["read"])
    store.create_request(fresh, authorization_params("read"))
    old_request = store.create_request(fresh, authorization_params("read"))
    _sql(store, "UPDATE oauth_requests SET expires_at = ? WHERE id = ?", now - 10, old_request)
    _sql(store, "INSERT INTO oauth_codes (hash, grant_id, data, expires_at) VALUES ('old', '', '{}', ?)", now - 10)

    counts = store.grants.housekeeping() | store.housekeeping()

    assert counts == {
        "login_codes": 1,
        "requests": 1,
        "codes": 1,
        "grants": 2,
        "tokens": 5,  # the active grant's expired access token and both tokens of each deleted grant
        "clients": 1,
    }
    remaining = {g.id for g in store.grants.list_grants(USER)}
    assert remaining == {active, recently_revoked}
    assert active_tokens.refresh_token is not None
    client_id = next(g.client_id for g in store.grants.list_grants(USER) if g.id == active)
    assert store.load_refresh(client_id, active_tokens.refresh_token) is not None
    assert store.get_client(abandoned.client_id) is None
    assert store.get_client(fresh.client_id) is not None
    assert store.get_client(client_id) is not None
    assert (_count(store, "login_codes"), _count(store, "oauth_requests"), _count(store, "oauth_codes")) == (1, 1, 1)
    assert store.grants.housekeeping() | store.housekeeping() == dict.fromkeys(counts, 0)
