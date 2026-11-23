"""Grants issued through the real OAuth store API, without the HTTP flow (for command and housekeeping tests)."""

import secrets

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from zerya_discord_mcp.access.oauth import SCOPE_READ, SCOPE_WRITE, OAuthStore

REDIRECT = "http://localhost:9999/callback"


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
