"""The search tool: query → hooks → text within a token budget."""

import hashlib
import sqlite3
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from zerya_discord_mcp.index.vocab import Vocabulary
from zerya_discord_mcp.query.directory import Directory
from zerya_discord_mcp.query.facets import Facets, compute_facets
from zerya_discord_mcp.query.hooks import Hook, build_hooks
from zerya_discord_mcp.query.parser import Group, ParsedQuery, QueryError, Term, parse
from zerya_discord_mcp.query.planner import Expansion, GroupHits, MatchKind, Plan, Planner
from zerya_discord_mcp.query.render import (
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    Formatter,
    MessageRow,
    collapse_gaps,
    fetch_after,
    fetch_before,
    fetch_range,
    message_link,
)
from zerya_discord_mcp.text import estimate_tokens, fold

MESSAGE_MAX_CHARS = 280
FOCAL_MAX_CHARS = 600


@dataclass(frozen=True, slots=True)
class SearchResult:
    text: str
    hook_count: int


def _query_hash(query: str, context: int) -> str:
    return hashlib.sha256(f"{' '.join(query.split())}|{context}".encode()).hexdigest()[:4]


def encode_cursor(offset: int, query: str, context: int) -> str:
    return f"c{offset}_{_query_hash(query, context)}"


def decode_cursor(cursor: str, query: str, context: int) -> int:
    body, _, digest = cursor.removeprefix("c").partition("_")
    if not body.isdigit() or digest != _query_hash(query, context):
        raise QueryError("This cursor belongs to a different query. Repeat the same q and context, or drop cursor.")
    return int(body)


class SearchEngine:
    def __init__(self, vocab: Vocabulary, tz: ZoneInfo, guild_id: int) -> None:
        self.vocab = vocab
        self.tz = tz
        self.guild_id = guild_id

    def search(
        self,
        conn: sqlite3.Connection,
        allowed: frozenset[int],
        q: str,
        *,
        context: int = 5,
        limit: int = 10,
        cursor: str | None = None,
        budget_tokens: int = 4000,
    ) -> SearchResult:
        context = max(0, min(context, 25))
        limit = max(1, min(limit, 50))
        offset = decode_cursor(cursor, q, context) if cursor else 0
        query = parse(q)
        self.vocab.load(conn)
        directory = Directory(conn, allowed)
        plan = Planner(conn, self.vocab, directory, self.tz).plan(query)
        hooks = build_hooks(conn, plan, context=context)
        fmt = Formatter(directory, self.tz, self.guild_id)

        header = [f"Query: {query}"]
        expanded = _expansion_line(plan)
        if expanded:
            header.append(f"Expanded: {expanded}")

        if len(hooks) < 3 and offset == 0:
            header.extend(_no_result_hints(plan, hooks, self.vocab, Planner(conn, self.vocab, directory, self.tz)))

        page = hooks[offset:]
        facets = compute_facets(conn, hooks, self.tz)
        body: list[str] = []
        used = estimate_tokens("\n".join(header)) + 120
        shown = 0
        for index, hook in enumerate(page[:limit], start=offset + 1):
            block = self._render_hook(conn, fmt, index, hook, context)
            cost = estimate_tokens(block)
            if shown and used + cost > budget_tokens:
                break
            body.append(block)
            used += cost
            shown += 1

        next_offset = offset + shown
        summary = f"{len(hooks)} hooks"
        if hooks:
            summary += f" (showing {offset + 1}–{next_offset}"
            summary += f", cursor: {encode_cursor(next_offset, q, context)})" if next_offset < len(hooks) else ")"
        header.append(summary)
        if hooks:
            header.extend(_facet_lines(facets, fmt, plan.query, len(hooks) > limit))

        text = "\n".join(header)
        if body:
            text += "\n\n" + UNTRUSTED_OPEN + "\n" + "\n\n".join(body) + "\n" + UNTRUSTED_CLOSE
        return SearchResult(text=text, hook_count=len(hooks))

    def _render_hook(self, conn: sqlite3.Connection, fmt: Formatter, index: int, hook: Hook, context: int) -> str:
        before = fetch_before(conn, hook.channel_id, hook.first_id, context)
        middle = fetch_range(conn, hook.channel_id, hook.first_id, hook.last_id)
        after = fetch_after(conn, hook.channel_id, hook.last_id, context)
        focal = set(hook.hit_ids)
        rows = collapse_gaps(middle, focal, keep=2)
        messages: list[MessageRow] = before + [r for r in rows if isinstance(r, MessageRow)] + after
        title = (
            f"[{index}] {fmt.directory.channel_label(hook.channel_id)} · {fmt.date(hook.first_id)} · "
            f"{fmt.participants(messages)} · {message_link(self.guild_id, hook.channel_id, hook.first_id)}"
        )
        lines = [title]
        start_date = fmt.date(messages[0].id) if messages else None
        lines += fmt.lines(before, max_chars=MESSAGE_MAX_CHARS, start_date=start_date)
        current = fmt.date(before[-1].id) if before else start_date
        for row in rows:
            if isinstance(row, int):
                lines.append(f"  … {row} messages …")
                continue
            lines += fmt.lines(
                [row], focal=focal, max_chars=MESSAGE_MAX_CHARS, focal_max_chars=FOCAL_MAX_CHARS, start_date=current
            )
            current = fmt.date(row.id)
        lines += fmt.lines(after, max_chars=MESSAGE_MAX_CHARS, start_date=current)
        return "\n".join(lines)


def _expansion_line(plan: Plan) -> str:
    parts: list[str] = []
    for group in plan.groups:
        if group.is_filter:
            continue
        extra = [
            e for e in group.expansions if e.kind is not MatchKind.EXACT and (e.hits or e.kind is MatchKind.CHANNEL)
        ]
        if not extra:
            continue
        rendered = ", ".join(_expansion_label(e) for e in extra[:6])
        parts.append(f"{group.group} → {rendered}")
    return " · ".join(parts)


def _expansion_label(expansion: Expansion) -> str:
    if expansion.kind is MatchKind.CHANNEL:
        return f"{expansion.display} (channel name)"
    return f"{expansion.display} ({expansion.kind.label}, {expansion.hits})"


def _no_result_hints(plan: Plan, hooks: list[Hook], vocab: Vocabulary, planner: Planner) -> list[str]:
    lines: list[str] = []
    counts: list[str] = []
    zero_groups: list[GroupHits] = []
    for group in plan.groups:
        n = len(group.hits)
        counts.append(f"{group.label}: {n} msgs" + (" + channel name" if group.channel_cover else ""))
        if n == 0 and not group.channel_cover and not group.is_filter:
            zero_groups.append(group)
    lines.append("Hits per term (readable channels, after filters): " + " · ".join(counts))
    for group in zero_groups:
        hints: list[str] = []
        for term in group.group.alternatives:
            word = fold(term.text)
            for variant in vocab.similar(word):
                if variant.term[:1] != word[:1]:
                    continue
                # Only suggest words that actually occur in channels this user can read.
                n = len(planner.group_hits(_single(variant.term), plan.scope).hits)
                if n:
                    hints.append(f"{variant.term} ({n})")
        if hints:
            lines.append(f"No hits for {group.label}. Similar words in the corpus: {', '.join(hints[:5])}")
        else:
            lines.append(f"No hits for {group.label}. Try a synonym, the English/Polish equivalent, or drop it.")
    if not hooks and not zero_groups and len(plan.groups) > 1:
        lines.append(
            "Every term occurs, but never close together. Try a larger context (e.g. context=15), "
            "fewer terms, or search terms separately."
        )
    return lines


def _single(term: str) -> Group:
    return Group((Term(term),))


def _facet_lines(facets: Facets, fmt: Formatter, query: ParsedQuery, many: bool) -> list[str]:
    channels = ", ".join(f"{fmt.directory.channel_label(cid)} {n}" for cid, n in facets.channels)
    authors = ", ".join(f"{fmt.directory.member_name(uid)} {n}" for uid, n in facets.authors)
    months = ", ".join(f"{month} {n}" for month, n in facets.months)
    lines = [f"Channels: {channels}", f"Authors: {authors}", f"Months: {months}"]
    if many:
        hints: list[str] = []
        used = {f.key for f in query.filters}
        if "in" not in used and facets.channels:
            hints.append(f"in:{fmt.directory.channel_label(facets.channels[0][0]).split(' › ')[0]}")
        if "from" not in used and facets.authors:
            member = fmt.directory.members.get(facets.authors[0][0])
            if member:
                hints.append(f"from:{member.username}")
        if "after" not in used and facets.months:
            hints.append(f"after:{facets.months[0][0]}")
        if hints:
            lines.append("Narrow with e.g.: " + ", ".join(hints))
    return lines
