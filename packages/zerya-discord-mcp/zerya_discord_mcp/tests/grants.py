"""Grants for tests: directly in the neutral store, or through the real MCP OAuth store API without HTTP."""

import secrets
import sqlite3
import time

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from zerya_discord_mcp.access.grants import SCOPE_READ, SCOPE_WRITE, GrantStore, insert_grant
from zerya_discord_mcp.mcp._oauth import OAuthStore

REDIRECT = "http://localhost:9999/callback"


def add_grant(store: GrantStore, user_id: int, *scopes: str, client_name: str | None = "Claude") -> str:
    """A grant as a transport would create it and the client then used, without any transport; returns its ID."""

    def apply(conn: sqlite3.Connection) -> str:
        now = int(time.time())
        scope_list = list(scopes or (SCOPE_READ,))
        grant_id = insert_grant(conn, user_id, secrets.token_hex(8), client_name, scope_list, now + 86400)
        conn.execute("UPDATE grants SET last_used_at = ? WHERE id = ?", (now, grant_id))
        return grant_id

    return store.db.write(apply)


def register_client(store: OAuthStore, name: str | None = "Claude") -> OAuthClientInformationFull:
    client = OAuthClientInformationFull(
        client_id=secrets.token_hex(8),
        client_name=name,
        redirect_uris=[AnyUrl(REDIRECT)],
        token_endpoint_auth_method="none",
    )
    store.save_client(client)
    return client


def authorization_params(*scopes: str) -> AuthorizationParams:
    return AuthorizationParams(
        state="xyz",
        scopes=list(scopes),
        code_challenge="x" * 43,
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
        resource=None,
    )


def issue_grant(
    store: OAuthStore, user_id: int, *scopes: str, client: OAuthClientInformationFull | None = None
) -> tuple[str, OAuthToken]:
    """Registers a client (unless given), approves its sign-in for ``user_id`` and returns (grant ID, tokens)."""
    client = client or register_client(store)
    request = store.get_request(store.create_request(client, authorization_params(*(scopes or (SCOPE_READ,)))))
    assert request is not None
    code = store.complete_request(request, user_id, [SCOPE_READ, SCOPE_WRITE])
    authorization = store.load_code(client.client_id, code)
    assert authorization is not None
    tokens = store.exchange_code(client, authorization)
    grant = store.verify_access(tokens.access_token)
    assert grant is not None
    return grant.id, tokens
