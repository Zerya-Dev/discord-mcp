"""Who is connected: one-time sign-in codes from /mcp login and the grants they lead to.

A grant is one connected client for one Discord user. /mcp list shows grants and /mcp revoke ends them. How a
grant is created (the OAuth flow) and how requests prove they hold one (tokens) belong to the transport that
issues them; this module only knows the grant itself, so it works without any transport installed.

Only SHA-256 hashes of codes are stored. What a grant can read is still decided per request from the user's live
Discord permissions; the grant only says who the user is.
"""

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from zerya_discord_mcp.index.db import Database

SCOPE_READ = "read"
SCOPE_WRITE = "write"
LOGIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
LOGIN_CODE_LENGTH = 8
LAST_USED_RESOLUTION = 60
ENDED_GRANT_RETENTION = 30 * 86400

GRANT_COLUMNS = "id, user_id, client_id, client_name, scopes, created_at, last_used_at, expires_at, revoked_at"


def hash_secret(value: str) -> str:
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


def grant_from_row(row: tuple[Any, ...]) -> Grant:
    """Maps the ``GRANT_COLUMNS`` of a row (in that order) to a Grant."""
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


def insert_grant(
    conn: sqlite3.Connection, user_id: int, client_id: str, client_name: str | None, scopes: list[str], expires_at: int
) -> str:
    """Creates a grant inside the caller's transaction (the transport issues its credentials in the same one)."""
    grant_id = secrets.token_hex(8)
    conn.execute(
        f"INSERT INTO grants ({GRANT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)",
        (grant_id, user_id, client_id, client_name, " ".join(scopes), int(time.time()), expires_at),
    )
    return grant_id


def extend_grant(conn: sqlite3.Connection, grant_id: str, expires_at: int) -> None:
    conn.execute("UPDATE grants SET expires_at = ? WHERE id = ?", (expires_at, grant_id))


class GrantStore:
    """Synchronous persistence for sign-in codes and grants (call from a worker thread in async code)."""

    def __init__(self, db: Database, *, login_ttl: int) -> None:
        self.db = db
        self.login_ttl = login_ttl

    # Sign-in codes (/mcp login) -------------------------------------------------------------------------

    def create_login_code(self, user_id: int) -> tuple[str, int]:
        code = "".join(secrets.choice(LOGIN_ALPHABET) for _ in range(LOGIN_CODE_LENGTH))
        now = int(time.time())
        expires = now + self.login_ttl

        def apply(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM login_codes WHERE expires_at < ? OR user_id = ?", (now, user_id))
            conn.execute(
                "INSERT INTO login_codes (hash, user_id, expires_at) VALUES (?, ?, ?)",
                (hash_secret(code), user_id, expires),
            )

        self.db.write(apply)
        return format_login_code(code), expires

    def consume_login_code(self, code: str) -> int | None:
        digest = hash_secret(normalize_login_code(code))

        def apply(conn: sqlite3.Connection) -> int | None:
            row = conn.execute("SELECT user_id, expires_at FROM login_codes WHERE hash = ?", (digest,)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM login_codes WHERE hash = ?", (digest,))
            return int(row[0]) if int(row[1]) >= time.time() else None

        return self.db.write(apply)

    # Grants ---------------------------------------------------------------------------------------------

    def list_grants(self, user_id: int) -> list[Grant]:
        rows = self.db.reader().execute(
            f"SELECT {GRANT_COLUMNS} FROM grants WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        )
        return [grant_from_row(r) for r in rows]

    def mark_used(self, grant: Grant) -> None:
        """Records use for /mcp list, at most once per LAST_USED_RESOLUTION to keep reads cheap."""
        now = int(time.time())
        if grant.last_used_at is None or now - grant.last_used_at >= LAST_USED_RESOLUTION:
            self.db.write(lambda c: c.execute("UPDATE grants SET last_used_at = ? WHERE id = ?", (now, grant.id)))

    def revoke_grant(self, grant_id: str) -> None:
        """Ends a grant. Credentials tied to it stop working because they are only valid for active grants."""
        now = int(time.time())
        self.db.write(
            lambda c: c.execute("UPDATE grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, grant_id))
        )

    def revoke_user_grants(self, user_id: int, grant_id: str | None) -> int:
        """Revokes one grant by ID, or all of the user's active grants when grant_id is None."""
        targets = [g.id for g in self.list_grants(user_id) if g.active and (grant_id is None or g.id == grant_id)]
        for gid in targets:
            self.revoke_grant(gid)
        return len(targets)

    # Housekeeping ---------------------------------------------------------------------------------------

    def housekeeping(self) -> dict[str, int]:
        """Drops expired sign-in codes and grants that ended over ENDED_GRANT_RETENTION ago."""
        now = int(time.time())

        def apply(conn: sqlite3.Connection) -> dict[str, int]:
            return {
                "login_codes": conn.execute("DELETE FROM login_codes WHERE expires_at < ?", (now,)).rowcount,
                "grants": conn.execute(
                    "DELETE FROM grants WHERE coalesce(revoked_at, expires_at) < ?", (now - ENDED_GRANT_RETENTION,)
                ).rowcount,
            }

        return self.db.write(apply)
