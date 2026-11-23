-- Channels, text channels, categories, forums and threads (threads and forum posts are channels too).
CREATE TABLE channels (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,             -- text | news | forum | category | voice | thread | forum_post | other
    parent_id INTEGER,
    topic TEXT,
    tags TEXT,                      -- forum tags (on posts: applied tag names), space separated
    position INTEGER NOT NULL DEFAULT 0,
    is_private INTEGER NOT NULL DEFAULT 0,  -- private thread
    owner_id INTEGER,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE members (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    display_name TEXT,
    nick TEXT,
    role_ids TEXT NOT NULL DEFAULT '',      -- space separated
    is_bot INTEGER NOT NULL DEFAULT 0,
    in_guild INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE roles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);

-- Message ID is a snowflake, so ID ranges are time ranges and channel_id+id keeps a channel in order.
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    reply_to_id INTEGER,
    edited_at INTEGER,
    content TEXT NOT NULL,          -- display text, mentions already resolved
    mention_ids TEXT NOT NULL DEFAULT '',   -- space separated user ids
    has_link INTEGER NOT NULL DEFAULT 0,
    has_file INTEGER NOT NULL DEFAULT 0,
    has_code INTEGER NOT NULL DEFAULT 0,
    reactions INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX messages_channel ON messages (channel_id, id);
CREATE INDEX messages_author ON messages (author_id, id);

-- Contentless FTS: the writer feeds already folded text, deletes go by rowid.
CREATE VIRTUAL TABLE fts_words USING fts5 (
    text, lemmas,
    content = '', contentless_delete = 1,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE fts_trigram USING fts5 (
    text,
    content = '', contentless_delete = 1,
    tokenize = 'trigram remove_diacritics 1'
);

CREATE VIRTUAL TABLE fts_vocab USING fts5vocab (fts_words, row);

CREATE TABLE cursors (
    channel_id INTEGER PRIMARY KEY,
    newest_id INTEGER,              -- highest message ID fetched by backfill
    oldest_id INTEGER,              -- lowest message ID fetched by backfill
    complete INTEGER NOT NULL DEFAULT 0     -- backfill reached the start of the channel (or the configured limit)
);

CREATE TABLE tokens (
    hash TEXT PRIMARY KEY,          -- SHA-256 of the token
    prefix TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    scope TEXT NOT NULL,            -- read | read+write
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    last_used_at INTEGER,
    revoked_at INTEGER
);

CREATE INDEX tokens_user ON tokens (user_id);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_prefix TEXT,
    user_id INTEGER,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    result_count INTEGER,
    duration_ms INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
