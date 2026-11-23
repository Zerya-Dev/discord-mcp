-- OAuth replaces static tokens: clients sign in through /authorize with a one-time code from /mcp login.
DROP TABLE tokens;

-- Dynamically registered OAuth clients (claude.ai, Claude Code, ...). info is the RFC 7591 client JSON.
CREATE TABLE oauth_clients (
    client_id TEXT PRIMARY KEY,
    info TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- One-time sign-in codes created by /mcp login (hash only).
CREATE TABLE login_codes (
    hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

-- /authorize requests waiting for the user to enter a sign-in code.
CREATE TABLE oauth_requests (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    params TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at INTEGER NOT NULL
);

-- A grant is one connected client for one user; /mcp list shows grants and /mcp revoke ends them.
CREATE TABLE grants (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    client_id TEXT NOT NULL,
    client_name TEXT,
    scopes TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
);

CREATE INDEX grants_user ON grants (user_id);

-- Authorization codes, access tokens and refresh tokens (hash only), all tied to a grant.
CREATE TABLE oauth_codes (
    hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    data TEXT NOT NULL,             -- PKCE challenge, redirect URI, scopes, resource
    expires_at INTEGER NOT NULL
);

CREATE TABLE oauth_tokens (
    hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,             -- access | refresh
    grant_id TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
);

CREATE INDEX oauth_tokens_grant ON oauth_tokens (grant_id);

ALTER TABLE audit_log RENAME COLUMN token_prefix TO grant_id;
