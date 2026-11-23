"""Query language: words, (a|b) groups, "phrases", -exclusions and key:value filters (Slack/Gmail style)."""

from dataclasses import dataclass, field

FILTER_KEYS = frozenset({"in", "from", "mentions", "before", "after", "has", "is"})
HAS_VALUES = frozenset({"link", "file", "code"})
IS_VALUES = frozenset({"thread"})


class QueryError(ValueError):
    """A query the agent should fix. The message says what is wrong and how to fix it."""


@dataclass(frozen=True, slots=True)
class Term:
    text: str
    phrase: bool = False

    def __str__(self) -> str:
        return f'"{self.text}"' if self.phrase else self.text


@dataclass(frozen=True, slots=True)
class Group:
    """Any of the alternatives must match. Groups are combined with AND."""

    alternatives: tuple[Term, ...]

    def __str__(self) -> str:
        if len(self.alternatives) == 1:
            return str(self.alternatives[0])
        return "(" + "|".join(str(a) for a in self.alternatives) + ")"


@dataclass(frozen=True, slots=True)
class Filter:
    key: str
    value: str
    negated: bool = False

    def __str__(self) -> str:
        value = f'"{self.value}"' if " " in self.value else self.value
        return f"{'-' if self.negated else ''}{self.key}:{value}"


@dataclass(slots=True)
class ParsedQuery:
    groups: list[Group] = field(default_factory=list[Group])
    excludes: list[Term] = field(default_factory=list[Term])
    filters: list[Filter] = field(default_factory=list[Filter])

    def __str__(self) -> str:
        parts = [str(g) for g in self.groups]
        parts += [f"-{t}" for t in self.excludes]
        parts += [str(f) for f in self.filters]
        return " ".join(parts)

    @property
    def is_empty(self) -> bool:
        return not self.groups and not self.filters


class _Reader:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def done(self) -> bool:
        return self.pos >= len(self.text)

    def skip_space(self) -> None:
        while not self.done() and self.text[self.pos].isspace():
            self.pos += 1

    def quoted(self) -> str:
        start = self.pos
        self.pos += 1  # opening quote
        end = self.text.find('"', self.pos)
        if end == -1:
            raise QueryError(f'Unclosed quote at "{self.text[start:]}". Close the phrase with a second ".')
        value = self.text[self.pos : end]
        self.pos = end + 1
        return value

    def bare(self, stop: str = "") -> str:
        start = self.pos
        while not self.done() and not self.text[self.pos].isspace() and self.text[self.pos] not in stop:
            self.pos += 1
        return self.text[start : self.pos]


def _term(text: str, phrase: bool) -> Term:
    text = text.strip()
    if not text:
        raise QueryError("Empty term. Remove the empty quotes or parentheses.")
    return Term(text=text, phrase=phrase)


def _group(reader: _Reader) -> Group:
    start = reader.pos
    reader.pos += 1  # (
    alternatives: list[Term] = []
    while True:
        reader.skip_space()
        ch = reader.peek()
        if not ch:
            raise QueryError(f'Unclosed group "{reader.text[start:]}". Close it with ")", e.g. (logo|logotyp).')
        if ch == ")":
            reader.pos += 1
            break
        if ch == "|":
            reader.pos += 1
            continue
        if ch == '"':
            alternatives.append(_term(reader.quoted(), phrase=True))
        else:
            alternatives.append(_term(reader.bare(stop="|)"), phrase=False))
    if not alternatives:
        raise QueryError("Empty group (). Put alternatives inside, e.g. (fundacja|foundation).")
    return Group(tuple(alternatives))


def _filter(key: str, value: str, negated: bool) -> Filter:
    if not value:
        raise QueryError(f"Missing value for {key}:. Example: {key}:{_EXAMPLES[key]}")
    if key == "has" and value not in HAS_VALUES:
        raise QueryError(f"Unknown has:{value}. Use one of: has:link, has:file, has:code.")
    if key == "is" and value not in IS_VALUES:
        raise QueryError(f"Unknown is:{value}. Use is:thread.")
    return Filter(key=key, value=value, negated=negated)


_EXAMPLES = {
    "in": "#general",
    "from": "ola",
    "mentions": "wiktor",
    "before": "2026-09-01",
    "after": "2026-09",
    "has": "link",
    "is": "thread",
}


def parse(text: str) -> ParsedQuery:
    query = ParsedQuery()
    reader = _Reader(text)
    while True:
        reader.skip_space()
        if reader.done():
            break
        negated = False
        if reader.peek() == "-" and reader.pos + 1 < len(text) and not text[reader.pos + 1].isspace():
            negated = True
            reader.pos += 1

        ch = reader.peek()
        if ch == '"':
            term = _term(reader.quoted(), phrase=True)
            if negated:
                query.excludes.append(term)
            else:
                query.groups.append(Group((term,)))
            continue
        if ch == "(":
            if negated:
                raise QueryError("Excluding a group is not supported. Exclude words one by one: -lego -duplo.")
            query.groups.append(_group(reader))
            continue

        token_start = reader.pos
        token = reader.bare()
        key, sep, value = token.partition(":")
        if sep and key.lower() in FILTER_KEYS:
            if value.startswith('"'):
                reader.pos = token_start + len(key) + 1
                value = reader.quoted()
            query.filters.append(_filter(key.lower(), value.strip(), negated))
            continue
        if "|" in token:
            if negated:
                raise QueryError("Excluding a group is not supported. Exclude words one by one: -lego -duplo.")
            alternatives = tuple(_term(part, phrase=False) for part in token.split("|") if part)
            query.groups.append(Group(alternatives))
            continue
        if negated:
            query.excludes.append(_term(token, phrase=False))
        else:
            query.groups.append(Group((_term(token, phrase=False),)))

    if query.is_empty:
        if query.excludes:
            raise QueryError("A query cannot consist of exclusions only. Add at least one word or filter.")
        raise QueryError("Empty query. Example: (fundacja|foundation) logo in:#grafika after:2026-09")
    return query
