"""Turns a parsed query into hit lists per term group.

The channel filter (channels the user may read) is part of every SQL statement, so nothing outside it is
ever counted, ranked or suggested.
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from zoneinfo import ZoneInfo

from zerya_discord_mcp.index.models import snowflake_at
from zerya_discord_mcp.index.vocab import Vocabulary
from zerya_discord_mcp.query.directory import Directory
from zerya_discord_mcp.query.parser import Group, ParsedQuery, QueryError, Term
from zerya_discord_mcp.text import fold, fts_fold, is_typo_variant, lemma, words

logger = logging.getLogger("zerya_discord_mcp.query")

HIT_CAP = 4000  # per expansion; the most recent hits win when a term is very common
MAX_SNOWFLAKE = (1 << 63) - 1


class MatchKind(IntEnum):
    """How a message matched a term. Lower is better."""

    EXACT = 0
    LEMMA = 1
    PREFIX = 2
    FUZZY = 3
    FRAGMENT = 4
    CHANNEL = 5

    @property
    def weight(self) -> float:
        return _WEIGHTS[self]

    @property
    def label(self) -> str:
        return _LABELS[self]


_WEIGHTS = {
    MatchKind.EXACT: 1.0,
    MatchKind.LEMMA: 0.85,
    MatchKind.PREFIX: 0.7,
    MatchKind.FUZZY: 0.6,
    MatchKind.FRAGMENT: 0.5,
    MatchKind.CHANNEL: 0.5,
}
_LABELS = {
    MatchKind.EXACT: "exact",
    MatchKind.LEMMA: "lemma",
    MatchKind.PREFIX: "prefix",
    MatchKind.FUZZY: "spelling",
    MatchKind.FRAGMENT: "fragment",
    MatchKind.CHANNEL: "channel",
}


@dataclass(frozen=True, slots=True)
class Expansion:
    """A form a term was matched in, shown to the agent so it knows what the engine searched for."""

    display: str
    kind: MatchKind
    hits: int


@dataclass(slots=True)
class GroupHits:
    group: Group
    hits: dict[int, MatchKind] = field(default_factory=dict[int, MatchKind])  # message id -> best match
    channels: dict[int, int] = field(default_factory=dict[int, int])  # message id -> channel id
    channel_cover: set[int] = field(default_factory=set[int])  # channels whose name matches the group
    expansions: list[Expansion] = field(default_factory=list[Expansion])
    is_filter: bool = False

    def add(self, rows: list[tuple[int, int]], kind: MatchKind) -> int:
        new = 0
        for mid, cid in rows:
            previous = self.hits.get(mid)
            if previous is None:
                new += 1
                self.hits[mid] = kind
                self.channels[mid] = cid
            elif kind < previous:
                self.hits[mid] = kind
        return new

    @property
    def label(self) -> str:
        return "filters" if self.is_filter else str(self.group)


@dataclass(slots=True)
class Scope:
    """Where to search: channels and an ID (= time) range, plus message-level filters."""

    channel_ids: frozenset[int]
    min_id: int = 0
    max_id: int = MAX_SNOWFLAKE
    author_ids: set[int] = field(default_factory=set[int])
    exclude_author_ids: set[int] = field(default_factory=set[int])
    mention_ids: set[int] = field(default_factory=set[int])
    has: set[str] = field(default_factory=set[str])
    has_not: set[str] = field(default_factory=set[str])

    @property
    def has_message_filters(self) -> bool:
        return bool(self.author_ids or self.exclude_author_ids or self.mention_ids or self.has or self.has_not)


@dataclass(slots=True)
class Plan:
    query: ParsedQuery
    scope: Scope
    groups: list[GroupHits]
    excluded_ids: set[int]


_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$")
_RELATIVE_RE = re.compile(r"^(\d+)([dwmy])$")


def parse_date(value: str, tz: ZoneInfo, *, end: bool) -> datetime:
    """YYYY, YYYY-MM, YYYY-MM-DD, today, yesterday or relative 7d/2w/3m/1y. ``end`` gives the end of the period."""
    now = datetime.now(tz)
    lowered = value.lower()
    if lowered in ("today", "yesterday"):
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if lowered == "yesterday":
            day -= timedelta(days=1)
        return day + timedelta(days=1) if end else day
    relative = _RELATIVE_RE.match(lowered)
    if relative:
        amount, unit = int(relative.group(1)), relative.group(2)
        days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit] * amount
        return now - timedelta(days=days)
    match = _DATE_RE.match(value)
    if not match:
        raise QueryError(f"Bad date {value}. Use YYYY, YYYY-MM, YYYY-MM-DD, today, yesterday or 7d / 2w / 3m / 1y.")
    year, month, day = int(match.group(1)), int(match.group(2) or 1), int(match.group(3) or 1)
    try:
        start = datetime(year, month, day, tzinfo=tz)
    except ValueError as e:
        raise QueryError(f"Bad date {value}: {e}.") from e
    if not end:
        return start
    if match.group(3):
        return start + timedelta(days=1)
    if match.group(2):
        return datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=tz)
    return datetime(year + 1, 1, 1, tzinfo=tz)


def _fts_quote(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


class Planner:
    def __init__(
        self,
        conn: sqlite3.Connection,
        vocab: Vocabulary,
        directory: Directory,
        tz: ZoneInfo,
    ) -> None:
        self.conn = conn
        self.vocab = vocab
        self.directory = directory
        self.tz = tz

    # Scope ----------------------------------------------------------------------------------------------

    def resolve_scope(self, query: ParsedQuery) -> Scope:
        allowed = self.directory.allowed
        included: set[int] = set()
        excluded: set[int] = set()
        thread_only = False
        scope = Scope(channel_ids=allowed)
        for flt in query.filters:
            match flt.key:
                case "in":
                    channel = self.directory.resolve_channel(flt.value)
                    # A channel includes its threads and forum posts.
                    ids = {channel.id} | {c.id for c in self.directory.children(channel.id)}
                    (excluded if flt.negated else included).update(ids)
                case "from":
                    member = self.directory.resolve_member(flt.value)
                    (scope.exclude_author_ids if flt.negated else scope.author_ids).add(member.id)
                case "mentions":
                    scope.mention_ids.add(self.directory.resolve_member(flt.value).id)
                case "after":
                    scope.min_id = max(scope.min_id, snowflake_at(parse_date(flt.value, self.tz, end=False)))
                case "before":
                    scope.max_id = min(scope.max_id, snowflake_at(parse_date(flt.value, self.tz, end=False)) - 1)
                case "has":
                    (scope.has_not if flt.negated else scope.has).add(flt.value)
                case "is":
                    thread_only = not flt.negated
                case _:
                    raise QueryError(f"Unsupported filter {flt}.")
        channels = set(included) if included else set(allowed)
        channels -= excluded
        if thread_only:
            channels = {cid for cid in channels if (c := self.directory.channels.get(cid)) and c.kind.is_thread}
        scope.channel_ids = frozenset(channels & allowed)
        if scope.min_id > scope.max_id:
            raise QueryError("The date range is empty: after: is later than before:.")
        return scope

    def _where(self, scope: Scope, *, message_filters: bool) -> tuple[str, list[object]]:
        clauses = ["m.channel_id IN (SELECT value FROM json_each(?))", "m.id BETWEEN ? AND ?"]
        params: list[object] = [json.dumps(sorted(scope.channel_ids)), scope.min_id, scope.max_id]
        if message_filters:
            if scope.author_ids:
                clauses.append("m.author_id IN (SELECT value FROM json_each(?))")
                params.append(json.dumps(sorted(scope.author_ids)))
            if scope.exclude_author_ids:
                clauses.append("m.author_id NOT IN (SELECT value FROM json_each(?))")
                params.append(json.dumps(sorted(scope.exclude_author_ids)))
            for uid in scope.mention_ids:
                clauses.append("(' ' || m.mention_ids || ' ') LIKE ?")
                params.append(f"% {uid} %")
            for flag in scope.has:
                clauses.append(f"m.has_{flag} = 1")
            for flag in scope.has_not:
                clauses.append(f"m.has_{flag} = 0")
        return " AND ".join(clauses), params

    # Hits -----------------------------------------------------------------------------------------------

    def _fts(self, table: str, match: str, scope: Scope) -> list[tuple[int, int]]:
        where, params = self._where(scope, message_filters=False)
        # CROSS JOIN keeps FTS as the outer loop: it yields rowids newest first and the scan stops at the cap
        # instead of matching, joining and sorting every hit of a common word.
        sql = (
            f"SELECT m.id, m.channel_id FROM {table} f CROSS JOIN messages m ON m.id = f.rowid "
            f"WHERE {table} MATCH ? AND {where} ORDER BY f.rowid DESC LIMIT {HIT_CAP}"
        )
        try:
            return [(int(a), int(b)) for a, b in self.conn.execute(sql, [match, *params])]
        except sqlite3.OperationalError as e:
            logger.warning("FTS query %r on %s failed: %s", match, table, e)
            return []

    def filter_hits(self, scope: Scope) -> list[tuple[int, int]]:
        where, params = self._where(scope, message_filters=True)
        sql = f"SELECT m.id, m.channel_id FROM messages m WHERE {where} ORDER BY m.id DESC LIMIT {HIT_CAP}"
        return [(int(a), int(b)) for a, b in self.conn.execute(sql, params)]

    def _term_hits(self, term: Term, scope: Scope, out: GroupHits) -> None:
        tokens = [fold(w) for w in words(fts_fold(term.text))]
        if not tokens:
            return
        if term.phrase or len(tokens) > 1:
            phrase = " ".join(tokens)
            n = out.add(self._fts("fts_words", "text : " + _fts_quote(phrase), scope), MatchKind.EXACT)
            out.expansions.append(Expansion(f'"{phrase}"', MatchKind.EXACT, n))
            if not term.phrase and len(term.text) >= 3:
                # Paths, file names and hyphenated words: match the raw string as a substring.
                n = out.add(self._fts("fts_trigram", _fts_quote(fts_fold(term.text)), scope), MatchKind.FRAGMENT)
                if n:
                    out.expansions.append(Expansion(term.text, MatchKind.FRAGMENT, n))
            return

        word = tokens[0]
        n = out.add(self._fts("fts_words", "text : " + _fts_quote(word), scope), MatchKind.EXACT)
        out.expansions.append(Expansion(word, MatchKind.EXACT, n))

        lem = lemma(term.text)
        n = out.add(self._fts("fts_words", "{text lemmas} : " + _fts_quote(lem), scope), MatchKind.LEMMA)
        if n:
            out.expansions.append(Expansion(lem if lem != word else f"{word} (forms)", MatchKind.LEMMA, n))

        if len(word) >= 5:
            n = out.add(self._fts("fts_words", "text : " + _fts_quote(word) + " *", scope), MatchKind.PREFIX)
            if n:
                out.expansions.append(Expansion(f"{word}*", MatchKind.PREFIX, n))

        for variant in self.vocab.variants(word):
            if not is_typo_variant(word, variant.term):
                continue
            n = out.add(self._fts("fts_words", "text : " + _fts_quote(variant.term), scope), MatchKind.FUZZY)
            if n:
                out.expansions.append(Expansion(variant.term, MatchKind.FUZZY, n))

        if len(out.hits) < 3 and len(word) >= 4:
            n = out.add(self._fts("fts_trigram", _fts_quote(word), scope), MatchKind.FRAGMENT)
            if n:
                out.expansions.append(Expansion(f"*{word}*", MatchKind.FRAGMENT, n))

        for channel_id in self.directory.channels_matching_word(word) + (
            self.directory.channels_matching_word(lem) if lem != word else []
        ):
            if channel_id in scope.channel_ids and channel_id not in out.channel_cover:
                out.channel_cover.add(channel_id)
                out.expansions.append(Expansion(self.directory.channel_label(channel_id), MatchKind.CHANNEL, 0))

    def group_hits(self, group: Group, scope: Scope) -> GroupHits:
        out = GroupHits(group=group)
        for term in group.alternatives:
            self._term_hits(term, scope, out)
        return out

    def excluded_ids(self, terms: list[Term], scope: Scope) -> set[int]:
        out: set[int] = set()
        for term in terms:
            tokens = [fold(w) for w in words(fts_fold(term.text))]
            if not tokens:
                continue
            if term.phrase or len(tokens) > 1:
                match = "text : " + _fts_quote(" ".join(tokens))
            else:
                match = "{text lemmas} : (" + _fts_quote(tokens[0]) + " OR " + _fts_quote(lemma(term.text)) + ")"
            out.update(mid for mid, _ in self._fts("fts_words", match, scope))
        return out

    def plan(self, query: ParsedQuery) -> Plan:
        scope = self.resolve_scope(query)
        groups = [self.group_hits(group, scope) for group in query.groups]
        if scope.has_message_filters or not groups:
            filter_group = GroupHits(group=Group(()), is_filter=True)
            filter_group.add(self.filter_hits(scope), MatchKind.EXACT)
            groups.append(filter_group)
        excluded = self.excluded_ids(query.excludes, scope)
        for group in groups:
            for mid in excluded & group.hits.keys():
                del group.hits[mid]
                del group.channels[mid]
        return Plan(query=query, scope=scope, groups=groups, excluded_ids=excluded)
