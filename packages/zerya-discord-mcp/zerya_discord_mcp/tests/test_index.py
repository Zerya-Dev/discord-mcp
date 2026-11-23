from pathlib import Path

import pytest

from zerya_discord_mcp.index import writer
from zerya_discord_mcp.index.db import Database
from zerya_discord_mcp.index.models import MessageRecord
from zerya_discord_mcp.index.vocab import Vocabulary


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "i.sqlite3")
    db.open()
    return db


def _match(db: Database, table: str, q: str) -> list[int]:
    return [int(r[0]) for r in db.reader().execute(f"SELECT rowid FROM {table} WHERE {table} MATCH ?", (q,))]


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.migrate()
    assert db.reader().execute("PRAGMA user_version").fetchone()[0] == 3


def test_edit_reindexes_and_delete_forgets_content(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.write(
        lambda c: writer.upsert_messages(c, [MessageRecord(id=1, channel_id=9, author_id=1, content="stary tekst")])
    )
    assert _match(db, "fts_words", "stary") == [1]
    db.write(
        lambda c: writer.upsert_messages(c, [MessageRecord(id=1, channel_id=9, author_id=1, content="nowy tekst")])
    )
    assert _match(db, "fts_words", "stary") == []
    assert _match(db, "fts_words", "nowy") == [1]
    db.write(lambda c: writer.delete_messages(c, [1]))
    assert _match(db, "fts_words", "nowy") == []
    assert _match(db, "fts_trigram", '"tekst"') == []
    assert db.reader().execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_polish_folding_and_lemmas(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.write(
        lambda c: writer.upsert_messages(c, [MessageRecord(id=5, channel_id=9, author_id=1, content="Łódź i fundacji")])
    )
    assert _match(db, "fts_words", "lodz") == [5]
    assert _match(db, "fts_words", "lemmas : fundacja") == [5]


def test_cursor_tracks_extremes(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.write(lambda c: writer.save_cursor(c, 9, newest_id=50, oldest_id=40))
    db.write(lambda c: writer.save_cursor(c, 9, newest_id=45, oldest_id=10))
    db.write(lambda c: writer.save_cursor(c, 9, complete=True))
    assert db.reader().execute("SELECT newest_id, oldest_id, complete FROM cursors").fetchone() == (50, 10, 1)


def test_vocabulary_variants(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.write(
        lambda c: writer.upsert_messages(
            c, [MessageRecord(id=1, channel_id=9, author_id=1, content="wikipopa deployment")]
        )
    )
    vocab = Vocabulary()
    vocab.load(db.reader(), force=True)
    assert [v.term for v in vocab.variants("wikipop")] == ["wikipopa"]
    assert vocab.variants("logo") == []  # short words never get spelling variants


def test_vocabulary_loads_on_a_freshly_booted_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # time.monotonic() counts from boot: right after a reboot (or on a new CI runner) it is small.
    monkeypatch.setattr("zerya_discord_mcp.index.vocab.time.monotonic", lambda: 5.0)
    db = _db(tmp_path)
    db.write(lambda c: writer.upsert_messages(c, [MessageRecord(id=1, channel_id=9, author_id=1, content="wikipopa")]))
    vocab = Vocabulary()
    vocab.load(db.reader())
    assert [v.term for v in vocab.variants("wikipop")] == ["wikipopa"]
