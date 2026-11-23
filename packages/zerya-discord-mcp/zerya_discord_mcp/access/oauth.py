"""OAuth 2.1 authorization server for MCP clients (claude.ai, Claude Code).

Flow: the client registers (RFC 7591) and sends the user to /authorize. We park the request and show a sign-in
page; the user runs /mcp login on Discord, which proves who they are, and pastes the one-time code. The page then
issues an authorization code bound to that Discord user, and the client exchanges it (with PKCE) for an access
token and a rotating refresh token. Every token belongs to a grant, which /mcp list shows and /mcp revoke ends.

Only SHA-256 hashes of codes and tokens are stored. What a grant can read is still decided per request from the
user's live Discord permissions; the grant only says who the user is.
"""

import asyncio
import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from zerya_discord_mcp.index.db import Database

SCOPE_READ = "read"
SCOPE_WRITE = "write"
LOGIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
LOGIN_CODE_LENGTH = 8
MAX_LOGIN_ATTEMPTS = 5
REQUEST_TTL = 900
AUTH_CODE_TTL = 300
LAST_USED_RESOLUTION = 60


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_login_code(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())


def format_login_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


@dataclass(frozen=True, slots=True)
class Grant:
    id: str
    user_id: int
    client_id: str
    client_name: str | None
    scopes: tuple[str, ...]
    created_at: int
    last_used_at: int | None
    expires_at: int
    revoked_at: int | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.expires_at > time.time()

    @property
    def can_write(self) -> bool:
        return SCOPE_WRITE in self.scopes


_GRANT_COLUMNS = "id, user_id, client_id, client_name, scopes, created_at, last_used_at, expires_at, revoked_at"


def _grant(row: tuple[Any, ...]) -> Grant:
    gid, user_id, client_id, name, scopes, created, used, expires, revoked = row
    return Grant(
        id=str(gid),
        user_id=int(user_id),
        client_id=str(client_id),
        client_name=str(name) if name else None,
        scopes=tuple(str(scopes).split()),
        created_at=int(created),
        last_used_at=int(used) if used is not None else None,
        expires_at=int(expires),
        revoked_at=int(revoked) if revoked is not None else None,
    )


@dataclass(frozen=True, slots=True)
class PendingRequest:
    id: str
    client: OAuthClientInformationFull
    params: AuthorizationParams


class OAuthStore:
    """Synchronous persistence for the OAuth flow (call from a worker thread in async code)."""

    def __init__(self, db: Database, *, access_ttl: int, refresh_ttl: int, login_ttl: int) -> None:
        self.db = db
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        self.login_ttl = login_ttl

    # Clients --------------------------------------------------------------------------------------------

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = self.db.reader().execute("SELECT info FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row[0]) if row else None

    def save_client(self, client: OAuthClientInformationFull) -> None:
        payload = client.model_dump_json(exclude_none=True)
        self.db.write(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO oauth_clients (client_id, info, created_at) VALUES (?, ?, ?)",
                (client.client_id, payload, int(time.time())),
            )
        )

    # Sign-in codes (/mcp login) -------------------------------------------------------------------------

    def create_login_code(self, user_id: int) -> tuple[str, int]:
        code = "".join(secrets.choice(LOGIN_ALPHABET) for _ in range(LOGIN_CODE_LENGTH))
        now = int(time.time())
        expires = now + self.login_ttl

        def apply(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM login_codes WHERE expires_at < ? OR user_id = ?", (now, user_id))
            conn.execute(
                "INSERT INTO login_codes (hash, user_id, expires_at) VALUES (?, ?, ?)", (_hash(code), user_id, expires)
            )

        self.db.write(apply)
        return format_login_code(code), expires

    def consume_login_code(self, code: str) -> int | None:
        digest = _hash(normalize_login_code(code))

        def apply(conn: sqlite3.Connection) -> int | None:
            row = conn.execute("SELECT user_id, expires_at FROM login_codes WHERE hash = ?", (digest,)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM login_codes WHERE hash = ?", (digest,))
            return int(row[0]) if int(row[1]) >= time.time() else None

        return self.db.write(apply)

    # Pending /authorize requests ------------------------------------------------------------------------

    def create_request(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(24)
        now = int(time.time())

        def apply(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM oauth_requests WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO oauth_requests (id, client_id, params, expires_at) VALUES (?, ?, ?, ?)",
                (request_id, client.client_id, params.model_dump_json(), now + REQUEST_TTL),
            )

        self.db.write(apply)
        return request_id

    def get_request(self, request_id: str) -> PendingRequest | None:
        row = (
            self.db
            .reader()
            .execute("SELECT client_id, params, expires_at FROM oauth_requests WHERE id = ?", (request_id,))
            .fetchone()
        )
        if row is None or int(row[2]) < time.time():
            return None
        client = self.get_client(str(row[0]))
        if client is None:
            return None
        return PendingRequest(request_id, client, AuthorizationParams.model_validate_json(row[1]))

    def fail_request(self, request_id: str) -> int:
        """Counts a wrong code; returns attempts left (the request is dropped at zero)."""

        def apply(conn: sqlite3.Connection) -> int:
            conn.execute("UPDATE oauth_requests SET attempts = attempts + 1 WHERE id = ?", (request_id,))
            row = conn.execute("SELECT attempts FROM oauth_requests WHERE id = ?", (request_id,)).fetchone()
            left = MAX_LOGIN_ATTEMPTS - int(row[0]) if row else 0
            if left <= 0:
                conn.execute("DELETE FROM oauth_requests WHERE id = ?", (request_id,))
            return max(left, 0)

        return self.db.write(apply)

    def complete_request(self, request: PendingRequest, user_id: int, supported_scopes: list[str]) -> str:
        """Turns an approved request into an authorization code for the client.

        Requested scopes we do not support are dropped; read access is always granted.
        """
        code = secrets.token_urlsafe(32)
        params = request.params
        scopes = sorted({SCOPE_READ} | {s for s in params.scopes or [] if s in supported_scopes})
        data = {
            "client_id": request.client.client_id,
            "client_name": request.client.client_name,
            "user_id": user_id,
            "scopes": scopes,
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource,
        }

        def apply(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM oauth_requests WHERE id = ?", (request.id,))
            conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (int(time.time()),))
            conn.execute(
                "INSERT INTO oauth_codes (hash, grant_id, data, expires_at) VALUES (?, '', ?, ?)",
                (_hash(code), json.dumps(data), int(time.time()) + AUTH_CODE_TTL),
            )

        self.db.write(apply)
        return code

    # Authorization codes --------------------------------------------------------------------------------

    def load_code(self, client_id: str, code: str) -> AuthorizationCode | None:
        row = (
            self.db
            .reader()
            .execute("SELECT data, expires_at FROM oauth_codes WHERE hash = ?", (_hash(code),))
            .fetchone()
        )
        if row is None:
            return None
        data = json.loads(row[0])
        if data["client_id"] != client_id:
            return None
        return AuthorizationCode(
            code=code,
            scopes=list(data["scopes"]),
            expires_at=float(row[1]),
            client_id=client_id,
            code_challenge=data["code_challenge"],
            redirect_uri=data["redirect_uri"],
            redirect_uri_provided_explicitly=bool(data["redirect_uri_provided_explicitly"]),
            resource=data.get("resource"),
            subject=str(data["user_id"]),
        )

    def exchange_code(self, client: OAuthClientInformationFull, code: AuthorizationCode) -> OAuthToken:
        grant_id = secrets.token_hex(8)
        now = int(time.time())

        def apply(conn: sqlite3.Connection) -> OAuthToken:
            deleted = conn.execute("DELETE FROM oauth_codes WHERE hash = ?", (_hash(code.code),)).rowcount
            if deleted == 0:
                raise TokenError("invalid_grant", "authorization code already used")
            conn.execute(
                f"INSERT INTO grants ({_GRANT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)",
                (
                    grant_id,
                    int(code.subject or 0),
                    client.client_id,
                    client.client_name,
                    " ".join(code.scopes),
                    now,
                    now + self.refresh_ttl,
                ),
            )
            return self._issue(conn, grant_id, code.scopes)

        return self.db.write(apply)

    # Tokens ---------------------------------------------------------------------------------------------

    def _issue(self, conn: sqlite3.Connection, grant_id: str, scopes: list[str]) -> OAuthToken:
        now = int(time.time())
        access = "zdm_at_" + secrets.token_urlsafe(32)
        refresh = "zdm_rt_" + secrets.token_urlsafe(32)
        conn.executemany(
            "INSERT INTO oauth_tokens (hash, kind, grant_id, expires_at) VALUES (?, ?, ?, ?)",
            [
                (_hash(access), "access", grant_id, now + self.access_ttl),
                (_hash(refresh), "refresh", grant_id, now + self.refresh_ttl),
            ],
        )
        conn.execute("UPDATE grants SET expires_at = ? WHERE id = ?", (now + self.refresh_ttl, grant_id))
        conn.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (now,))
        return OAuthToken(
            access_token=access, expires_in=self.access_ttl, scope=" ".join(scopes), refresh_token=refresh
        )

    def _token_grant(self, token: str, kind: str) -> tuple[Grant, int, int | None] | None:
        row = (
            self.db
            .reader()
            .execute(
                f"SELECT t.expires_at, t.revoked_at, {', '.join('g.' + c for c in _GRANT_COLUMNS.split(', '))} "
                "FROM oauth_tokens t JOIN grants g ON g.id = t.grant_id WHERE t.hash = ? AND t.kind = ?",
                (_hash(token), kind),
            )
            .fetchone()
        )
        if row is None:
            return None
        return _grant(row[2:]), int(row[0]), int(row[1]) if row[1] is not None else None

    def load_refresh(self, client_id: str, token: str) -> RefreshToken | None:
        found = self._token_grant(token, "refresh")
        if found is None:
            return None
        grant, expires, revoked = found
        if revoked is not None:
            # A rotated refresh token came back: someone holds a copy. End the whole grant.
            self.revoke_grant(grant.id)
            return None
        if grant.client_id != client_id or not grant.active or expires < time.time():
            return None
        return RefreshToken(
            token=token, client_id=client_id, scopes=list(grant.scopes), expires_at=expires, subject=str(grant.user_id)
        )

    def exchange_refresh(self, refresh: RefreshToken, scopes: list[str]) -> OAuthToken:
        found = self._token_grant(refresh.token, "refresh")
        if found is None:
            raise TokenError("invalid_grant", "refresh token not found")
        grant = found[0]
        granted = [s for s in (scopes or list(grant.scopes)) if s in grant.scopes]

        def apply(conn: sqlite3.Connection) -> OAuthToken:
            now = int(time.time())
            # Conditional update: of two concurrent refreshes with the same token only one wins.
            rotated = conn.execute(
                "UPDATE oauth_tokens SET revoked_at = ? WHERE hash = ? AND revoked_at IS NULL",
                (now, _hash(refresh.token)),
            ).rowcount
            if rotated == 0:
                raise TokenError("invalid_grant", "refresh token already used")
            return self._issue(conn, grant.id, granted)

        return self.db.write(apply)

    def verify_access(self, token: str) -> Grant | None:
        found = self._token_grant(token, "access")
        if found is None:
            return None
        grant, expires, revoked = found
        if revoked is not None or expires < time.time() or not grant.active:
            return None
        now = int(time.time())
        if grant.last_used_at is None or now - grant.last_used_at >= LAST_USED_RESOLUTION:
            self.db.write(lambda c: c.execute("UPDATE grants SET last_used_at = ? WHERE id = ?", (now, grant.id)))
        return grant

    def access_token(self, token: str) -> AccessToken | None:
        found = self._token_grant(token, "access")
        if found is None:
            return None
        grant, expires, _ = found
        return AccessToken(
            token=token,
            client_id=grant.client_id,
            scopes=list(grant.scopes),
            expires_at=expires,
            subject=str(grant.user_id),
        )

    def revoke_token(self, token: str) -> None:
        for kind in ("access", "refresh"):
            found = self._token_grant(token, kind)
            if found is not None:
                self.revoke_grant(found[0].id)

    # Housekeeping ---------------------------------------------------------------------------------------

    def housekeeping(self) -> dict[str, int]:
        """Drops expired codes, requests and tokens, old ended grants and clients that never connected."""
        now = int(time.time())
        month_ago = now - 30 * 86400

        def apply(conn: sqlite3.Connection) -> dict[str, int]:
            counts = {
                "login_codes": conn.execute("DELETE FROM login_codes WHERE expires_at < ?", (now,)).rowcount,
                "requests": conn.execute("DELETE FROM oauth_requests WHERE expires_at < ?", (now,)).rowcount,
                "codes": conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (now,)).rowcount,
            }
            old_grants = [
                str(r[0])
                for r in conn.execute("SELECT id FROM grants WHERE coalesce(revoked_at, expires_at) < ?", (month_ago,))
            ]
            conn.executemany("DELETE FROM oauth_tokens WHERE grant_id = ?", [(g,) for g in old_grants])
            conn.executemany("DELETE FROM grants WHERE id = ?", [(g,) for g in old_grants])
            counts["grants"] = len(old_grants)
            counts["tokens"] = conn.execute(
                "DELETE FROM oauth_tokens WHERE expires_at < ? OR revoked_at < ?", (now, month_ago)
            ).rowcount
            counts["clients"] = conn.execute(
                "DELETE FROM oauth_clients WHERE created_at < ? AND client_id NOT IN (SELECT client_id FROM grants)",
                (now - 86400,),
            ).rowcount
            return counts

        return self.db.write(apply)

    # Grants ---------------------------------------------------------------------------------------------

    def list_grants(self, user_id: int) -> list[Grant]:
        rows = self.db.reader().execute(
            f"SELECT {_GRANT_COLUMNS} FROM grants WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        )
        return [_grant(r) for r in rows]

    def revoke_grant(self, grant_id: str) -> None:
        now = int(time.time())

        def apply(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, grant_id))
            conn.execute(
                "UPDATE oauth_tokens SET revoked_at = ? WHERE grant_id = ? AND revoked_at IS NULL", (now, grant_id)
            )

        self.db.write(apply)

    def revoke_user_grants(self, user_id: int, grant_id: str | None) -> int:
        """Revokes one grant by ID, or all of the user's active grants when grant_id is None."""
        targets = [g.id for g in self.list_grants(user_id) if g.active and (grant_id is None or g.id == grant_id)]
        for gid in targets:
            self.revoke_grant(gid)
        return len(targets)


class OAuthProvider:
    """``OAuthAuthorizationServerProvider`` for the MCP SDK's auth routes, backed by ``OAuthStore``."""

    def __init__(
        self,
        store: OAuthStore,
        login_url: str,
        supported_scopes: list[str],
        redirect_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self.store = store
        self.login_url = login_url
        self.supported_scopes = supported_scopes
        self.redirect_hosts = redirect_hosts

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client = await asyncio.to_thread(self.store.get_client, client_id)
        if client is None:
            return None
        # A client may request every scope the server supports now, even if it registered before write was
        # enabled: the grant's real scopes are decided (and shown to the user) on the sign-in page.
        registered = (client.scope or "").split()
        scopes = registered + [s for s in self.supported_scopes if s not in registered]
        return client.model_copy(update={"scope": " ".join(scopes)})

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await asyncio.to_thread(self.store.save_client, client_info)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        host = params.redirect_uri.host or ""
        if self.redirect_hosts and host not in self.redirect_hosts:
            raise AuthorizeError("unauthorized_client", f"redirects to {host} are not allowed on this server")
        request_id = await asyncio.to_thread(self.store.create_request, client, params)
        return f"{self.login_url}?request={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return await asyncio.to_thread(self.store.load_code, client.client_id, authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        return await asyncio.to_thread(self.store.exchange_code, client, authorization_code)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        return await asyncio.to_thread(self.store.load_refresh, client.client_id, refresh_token)

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        return await asyncio.to_thread(self.store.exchange_refresh, refresh_token, scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return await asyncio.to_thread(self.store.access_token, token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await asyncio.to_thread(self.store.revoke_token, token.token)
