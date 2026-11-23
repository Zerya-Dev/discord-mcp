"""Corpus vocabulary (from fts5vocab) used for spelling variants and "did you mean" hints.

The vocabulary covers every indexed channel. Callers must check that a variant has hits in the channels
the user may read before showing it, otherwise a word could leak from a private channel.
"""

import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

REFRESH_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class Variant:
    term: str
    distance: int
    doc_count: int


def max_distance(word: str) -> int:
    """Edit distance allowed for spelling variants. Short words only match exactly or by lemma."""
    if len(word) <= 4:
        return 0
    if len(word) <= 7:
        return 1
    return 2


class Vocabulary:
    def __init__(self) -> None:
        self._by_length: dict[int, list[str]] = {}
        self._counts: dict[str, int] = {}
        # None = never loaded, or invalidated. Never compare a monotonic clock to 0: it counts from boot, so
        # "0" can be less than REFRESH_SECONDS ago on a freshly started machine.
        self._loaded_at: float | None = None
        self._lock = threading.Lock()
        self._refreshing = threading.Lock()

    def invalidate(self) -> None:
        self._loaded_at = None

    def load(self, conn: sqlite3.Connection, *, force: bool = False) -> None:
        """Loads the vocabulary on first use; afterwards a stale copy is refreshed in a background thread."""
        if not force and self._counts and self._stale():
            if self._refreshing.acquire(blocking=False):
                path = conn.execute("PRAGMA database_list").fetchone()[2]
                threading.Thread(target=self._refresh_from, args=(path,), daemon=True, name="zdm-vocab").start()
            return
        self._load(conn, force=force)

    def _stale(self) -> bool:
        return self._loaded_at is None or time.monotonic() - self._loaded_at >= REFRESH_SECONDS

    def _refresh_from(self, path: str) -> None:
        try:
            conn = sqlite3.connect(path)
            try:
                self._load(conn, force=True)
            finally:
                conn.close()
        finally:
            self._refreshing.release()

    def _load(self, conn: sqlite3.Connection, *, force: bool) -> None:
        with self._lock:
            if not force and not self._stale():
                return
            counts: dict[str, int] = {}
            by_length: defaultdict[int, list[str]] = defaultdict(list)
            for term, doc in conn.execute("SELECT term, doc FROM fts_vocab"):
                if term.isdigit():
                    continue
                counts[term] = int(doc)
                by_length[len(term)].append(term)
            self._counts = counts
            self._by_length = dict(by_length)
            self._loaded_at = time.monotonic()

    def doc_count(self, term: str) -> int:
        return self._counts.get(term, 0)

    def _candidates(self, word: str, distance: int) -> list[str]:
        out: list[str] = []
        for length in range(len(word) - distance, len(word) + distance + 1):
            out.extend(self._by_length.get(length, ()))
        return out

    def variants(self, word: str, *, distance: int | None = None, limit: int = 6) -> list[Variant]:
        """Corpus terms within the allowed edit distance of ``word`` (a folded word), most frequent first."""
        allowed = max_distance(word) if distance is None else distance
        if allowed == 0:
            return []
        matches = process.extract(
            word,
            self._candidates(word, allowed),
            scorer=Levenshtein.distance,
            score_cutoff=allowed,
            limit=limit * 4,
        )
        found = [
            Variant(term=term, distance=int(dist), doc_count=self._counts.get(term, 0))
            for term, dist, _ in matches
            if term != word
        ]
        found.sort(key=lambda v: (v.distance, -v.doc_count))
        return found[:limit]

    def similar(self, word: str, *, limit: int = 5) -> list[Variant]:
        """Looser matching for hints when a term found nothing."""
        distance = 1 if len(word) <= 5 else 2
        return self.variants(word, distance=distance, limit=limit) if len(word) >= 3 else []
