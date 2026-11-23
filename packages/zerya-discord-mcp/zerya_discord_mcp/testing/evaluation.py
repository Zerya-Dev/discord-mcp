"""Eval runner: recall@k and MRR of search over a real index snapshot, without Discord.

An eval file (YAML) is a list of cases:

    - question: gdzie ustaliliśmy logo dla wikipopa
      queries: ["wikipop logo", "(logo|logotyp) fundacja"]   # what an agent would type, tried in order
      expected: [1418245318471434311]                        # message IDs that must be found

A case counts as found at rank r when an expected message lies inside the r-th hook (its context included) of
the first query that finds it.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from zerya_discord_mcp.index.vocab import Vocabulary
from zerya_discord_mcp.query.directory import Directory
from zerya_discord_mcp.query.hooks import Hook, build_hooks
from zerya_discord_mcp.query.parser import QueryError, parse
from zerya_discord_mcp.query.planner import Planner
from zerya_discord_mcp.query.render import fetch_after, fetch_before
from zerya_discord_mcp.query.search import SearchEngine
from zerya_discord_mcp.text import estimate_tokens


@dataclass(frozen=True, slots=True)
class EvalCase:
    question: str
    queries: list[str]
    expected: set[int]


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: EvalCase
    rank: int | None  # 1-based rank of the first hook containing an expected message
    query: str | None
    tokens: int


def load_cases(path: Path) -> list[EvalCase]:
    import yaml

    raw: list[dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [
        EvalCase(
            question=str(item["question"]),
            queries=[str(q) for q in item["queries"]],
            expected={int(e) for e in item["expected"]},
        )
        for item in raw
    ]


def _hook_ids(conn: sqlite3.Connection, hook: Hook, context: int) -> set[int]:
    ids = {
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM messages WHERE channel_id = ? AND id BETWEEN ? AND ?",
            (hook.channel_id, hook.first_id, hook.last_id),
        )
    }
    ids |= {m.id for m in fetch_before(conn, hook.channel_id, hook.first_id, context)}
    ids |= {m.id for m in fetch_after(conn, hook.channel_id, hook.last_id, context)}
    return ids


def run_eval(
    conn: sqlite3.Connection,
    cases: list[EvalCase],
    allowed: frozenset[int],
    *,
    guild_id: int,
    k: int = 10,
    context: int = 5,
    tz: str = "Europe/Warsaw",
) -> list[CaseResult]:
    vocab = Vocabulary()
    vocab.load(conn, force=True)
    zone = ZoneInfo(tz)
    engine = SearchEngine(vocab, zone, guild_id)
    directory = Directory(conn, allowed)
    results: list[CaseResult] = []
    for case in cases:
        found: tuple[int, str] | None = None
        tokens = 0
        for q in case.queries:
            try:
                plan = Planner(conn, vocab, directory, zone).plan(parse(q))
            except QueryError:
                continue
            hooks = build_hooks(conn, plan, context=context)[:k]
            tokens += estimate_tokens(engine.search(conn, allowed, q, context=context, limit=k).text)
            for rank, hook in enumerate(hooks, start=1):
                if case.expected & _hook_ids(conn, hook, context):
                    found = (rank, q)
                    break
            if found:
                break
        results.append(
            CaseResult(case=case, rank=found[0] if found else None, query=found[1] if found else None, tokens=tokens)
        )
    return results


def summarize(results: list[CaseResult], k: int = 10) -> str:
    if not results:
        return "No eval cases."
    recall = sum(1 for r in results if r.rank is not None and r.rank <= k) / len(results)
    mrr = sum(1 / r.rank for r in results if r.rank) / len(results)
    tokens = sum(r.tokens for r in results) / len(results)
    lines = [f"cases {len(results)} · recall@{k} {recall:.2f} · MRR {mrr:.2f} · avg tokens {tokens:.0f}"]
    for r in results:
        mark = f"#{r.rank} via {r.query!r}" if r.rank else "MISS"
        lines.append(f"  {mark:40} {r.case.question}")
    return "\n".join(lines)
