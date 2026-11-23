"""SQLite connection handling: one writer connection behind a lock, one reader per thread (WAL)."""

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

# contentless_delete needs 3.43, trigram remove_diacritics needs 3.45.
MIN_SQLITE_VERSION = (3, 45, 0)


class SqliteUnsupportedError(RuntimeError):
    pass


def check_sqlite() -> None:
    if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
        required = ".".join(map(str, MIN_SQLITE_VERSION))
        raise SqliteUnsupportedError(f"SQLite {sqlite3.sqlite_version} is too old, need {required}+")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram remove_diacritics 1')")
    except sqlite3.OperationalError as e:
        raise SqliteUnsupportedError(f"SQLite lacks FTS5 with the trigram tokenizer: {e}") from e
    finally:
        conn.close()


def _migrations() -> list[tuple[int, str]]:
    files = resources.files("zerya_discord_mcp.index.migrations")
    out: list[tuple[int, str]] = []
    for entry in files.iterdir():
        if entry.name.endswith(".sql"):
            out.append((int(entry.name.split("_", 1)[0]), entry.read_text(encoding="utf-8")))
    return sorted(out)


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -32000")


class Database:
    """Owns the library's SQLite file.

    Writes go through a single connection guarded by a lock (SQLite allows one writer anyway).
    Reads use a connection per thread so the MCP server thread never waits on the bot loop.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._write_lock = threading.Lock()
        self._writer: sqlite3.Connection | None = None
        self._local = threading.local()
        self._readers: list[sqlite3.Connection] = []
        self._readers_lock = threading.Lock()

    def open(self) -> None:
        check_sqlite()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        writer = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        _configure(writer)
        self._writer = writer
        self.migrate()

    @property
    def is_open(self) -> bool:
        return self._writer is not None

    def close(self) -> None:
        # Wait for an in-flight write (e.g. a cancelled backfill batch still running in its thread).
        with self._write_lock:
            self._close()

    def _close(self) -> None:
        with self._readers_lock:
            for conn in self._readers:
                conn.close()
            self._readers.clear()
        self._local = threading.local()
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    def migrate(self) -> None:
        writer = self._require_writer()
        with self._write_lock:
            current = int(writer.execute("PRAGMA user_version").fetchone()[0])
            for version, sql in _migrations():
                if version <= current:
                    continue
                try:
                    writer.executescript(f"BEGIN; {sql}; PRAGMA user_version = {version}; COMMIT;")
                except BaseException:
                    if writer.in_transaction:
                        writer.execute("ROLLBACK")
                    raise

    def _require_writer(self) -> sqlite3.Connection:
        if self._writer is None:
            raise RuntimeError("Database is not open")
        return self._writer

    def write[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Runs ``fn`` inside a write transaction."""
        writer = self._require_writer()
        with self._write_lock:
            writer.execute("BEGIN IMMEDIATE")
            try:
                result = fn(writer)
            except BaseException:
                writer.execute("ROLLBACK")
                raise
            writer.execute("COMMIT")
            return result

    def reader(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            _configure(conn)
            conn.execute("PRAGMA query_only = 1")
            self._local.conn = conn
            with self._readers_lock:
                self._readers.append(conn)
        return conn

    @contextmanager
    def snapshot(self) -> Iterator[sqlite3.Connection]:
        """A consistent read transaction (all queries see the same state)."""
        conn = self.reader()
        conn.execute("BEGIN")
        try:
            yield conn
        finally:
            conn.execute("COMMIT")
