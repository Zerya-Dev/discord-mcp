"""Text processing shared by indexing and querying.

Both sides must fold text the same way, otherwise a query term never meets the indexed token.
SQLite's ``unicode61 remove_diacritics 2`` tokenizer lowercases and strips most Polish diacritics
but keeps ``ł``, so we map ``ł`` to ``l`` ourselves before text reaches FTS5.
"""

import re
import unicodedata
from functools import lru_cache

import simplemma

WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STROKE_L = str.maketrans({"ł": "l", "Ł": "L"})
_LEMMA_LANGS = ("pl", "en")


def fts_fold(text: str) -> str:
    """Prepares text for FTS5: the tokenizer does the rest of the folding."""
    return text.translate(_STROKE_L)


def fold(word: str) -> str:
    """Folds a single word exactly like the FTS5 tokenizer does (used for vocabulary lookups)."""
    decomposed = unicodedata.normalize("NFKD", fts_fold(word).casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def words(text: str) -> list[str]:
    return WORD_RE.findall(text)


@lru_cache(maxsize=200_000)
def lemma(word: str) -> str:
    """Folded lemma of a word, or the folded word itself when the lemmatizer does not know it."""
    lowered = word.casefold()
    if len(lowered) < 3 or any(ch.isdigit() for ch in lowered):
        return fold(lowered)
    return fold(simplemma.lemmatize(lowered, lang=_LEMMA_LANGS))


@lru_cache(maxsize=50_000)
def is_known(word: str) -> bool:
    """Whether the lemmatizer's dictionaries know the word (so it is a real word, not a typo)."""
    return simplemma.is_known(word.casefold(), lang=_LEMMA_LANGS)


def is_typo_variant(query_word: str, variant: str) -> bool:
    """A corpus word close to the query is a plausible misspelling unless it is a different real word.

    ``funkcja`` is two edits from ``fundacja`` but a different dictionary word, so it is not a variant.
    ``foundacja`` or ``wikipopa`` are unknown to the dictionary, so they are.
    """
    return not is_known(variant) or lemma(variant) == lemma(query_word)


def lemmas_for_index(text: str) -> str:
    """Space-separated lemmas that differ from the words already present in the text."""
    seen: set[str] = set()
    out: list[str] = []
    for word in words(text):
        folded = fold(word)
        lem = lemma(word)
        if lem != folded and lem not in seen:
            seen.add(lem)
            out.append(lem)
    return " ".join(out)


def warm_up() -> None:
    """Loads the lemmatizer dictionaries (about a second) so the first query is not slow."""
    lemma("fundacji")
    lemma("deployments")
    is_known("fundacja")


def estimate_tokens(text: str) -> int:
    """Cheap token estimate; Polish text averages roughly 3.5 characters per token."""
    return max(1, int(len(text) / 3.5))
