"""Hook building: find places in a channel where every term group occurs within a few messages.

Terms of one conversation are usually spread over several short messages, so matching is done on a sliding
window of messages rather than on a single message. Overlapping windows merge into one hook.
"""

import itertools
import json
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field

from zerya_discord_mcp.index.models import snowflake_time
from zerya_discord_mcp.query.planner import GroupHits, MatchKind, Plan

MAX_HOOK_SPAN = 40  # messages; longer runs of hits are split into several hooks


@dataclass(slots=True)
class Hook:
    channel_id: int
    hit_ids: list[int]  # focal messages, ascending
    score: float
    start_pos: int = 0
    end_pos: int = 0
    kinds: dict[int, MatchKind] = field(default_factory=dict[int, MatchKind])

    @property
    def first_id(self) -> int:
        return self.hit_ids[0]

    @property
    def last_id(self) -> int:
        return self.hit_ids[-1]


@dataclass(slots=True)
class _Hit:
    id: int
    pos: int
    groups: dict[int, MatchKind]


def _relative_positions(conn: sqlite3.Connection, channel_id: int, ids: list[int], cap: int) -> list[int]:
    """Positions of hit messages relative to each other, counting at most ``cap`` messages between neighbours.

    Windows only care about distances up to ``cap``, so each gap is a bounded index range count instead of
    loading every message of the channel between the first and the last hit.
    """
    positions = [0]
    for previous, current in itertools.pairwise(ids):
        gap = conn.execute(
            "SELECT count(*) FROM (SELECT 1 FROM messages WHERE channel_id = ? AND id > ? AND id < ? LIMIT ?)",
            (channel_id, previous, current, cap),
        ).fetchone()[0]
        positions.append(positions[-1] + int(gap) + 1)
    return positions


def _recency(message_id: int, now: float) -> float:
    age_days = max(0.0, now - snowflake_time(message_id).timestamp()) / 86400
    return math.exp(-age_days / 365)


def _window_score(best: dict[int, MatchKind], group_count: int, span: int, context: int) -> float:
    coverage = sum(kind.weight for kind in best.values()) / group_count
    proximity = 1.0 / (1.0 + span / (2 * context + 1))
    return coverage * (0.6 + 0.4 * proximity)


def build_hooks(conn: sqlite3.Connection, plan: Plan, *, context: int) -> list[Hook]:
    groups: list[GroupHits] = plan.groups
    group_count = len(groups)
    if group_count == 0:
        return []

    by_channel: defaultdict[int, dict[int, dict[int, MatchKind]]] = defaultdict(dict)
    for gi, group in enumerate(groups):
        for mid, kind in group.hits.items():
            by_channel[group.channels[mid]].setdefault(mid, {})[gi] = kind

    reactions: dict[int, int] = {}
    now = time.time()
    hooks: list[Hook] = []
    for channel_id, messages in by_channel.items():
        covered_by_name = {gi for gi, g in enumerate(groups) if channel_id in g.channel_cover}
        present = {gi for groups_of in messages.values() for gi in groups_of} | covered_by_name
        if len(present) < group_count:
            continue
        ids = sorted(messages)
        # Gaps beyond this are "far" for both window matching (2*context) and hook merging (MAX_HOOK_SPAN).
        cap = max(2 * context, MAX_HOOK_SPAN) + 2
        positions = _relative_positions(conn, channel_id, ids, cap)
        hits = [_Hit(id=mid, pos=pos, groups=messages[mid]) for mid, pos in zip(ids, positions, strict=True)]
        windows = _minimal_windows(hits, group_count, covered_by_name, context)
        hooks.extend(_merge(channel_id, windows, hits, group_count, context))

    if hooks:
        focal = [h.last_id for h in hooks]
        for mid, count in conn.execute(
            "SELECT id, reactions FROM messages WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(focal),),
        ):
            reactions[int(mid)] = int(count)
    for hook in hooks:
        hook.score += 0.08 * _recency(hook.last_id, now) + 0.04 * min(1.0, reactions.get(hook.last_id, 0) / 5)
        hook.score += 0.03 * math.log1p(len(hook.hit_ids) - 1)
    hooks.sort(key=lambda h: (-h.score, -h.last_id))
    return hooks


def _minimal_windows(
    hits: list[_Hit], group_count: int, covered_by_name: set[int], context: int
) -> list[tuple[int, int, float, dict[int, MatchKind]]]:
    """For each start hit, the shortest run of hits within 2*context positions that covers every group."""
    out: list[tuple[int, int, float, dict[int, MatchKind]]] = []
    max_span = 2 * context
    n = len(hits)
    for i in range(n):
        best: dict[int, MatchKind] = dict.fromkeys(covered_by_name, MatchKind.CHANNEL)
        for j in range(i, n):
            if hits[j].pos - hits[i].pos > max_span:
                break
            for gi, kind in hits[j].groups.items():
                if gi not in best or kind < best[gi]:
                    best[gi] = kind
            if len(best) == group_count:
                if not any(kind is not MatchKind.CHANNEL for kind in best.values()):
                    break
                span = hits[j].pos - hits[i].pos
                out.append((i, j, _window_score(best, group_count, span, context), dict(best)))
                break
    return out


def _merge(
    channel_id: int,
    windows: list[tuple[int, int, float, dict[int, MatchKind]]],
    hits: list[_Hit],
    group_count: int,
    context: int,
) -> list[Hook]:
    hooks: list[Hook] = []
    current: Hook | None = None
    for i, j, score, _best in sorted(windows):
        start, end = hits[i].pos, hits[j].pos
        if current is not None and start <= current.end_pos + context and end - current.start_pos <= MAX_HOOK_SPAN:
            current.end_pos = max(current.end_pos, end)
            current.score = max(current.score, score)
            for k in range(i, j + 1):
                if hits[k].id not in current.kinds:
                    current.hit_ids.append(hits[k].id)
                current.kinds[hits[k].id] = min(hits[k].groups.values())
            continue
        current = Hook(channel_id=channel_id, hit_ids=[], score=score, start_pos=start, end_pos=end)
        for k in range(i, j + 1):
            current.hit_ids.append(hits[k].id)
            current.kinds[hits[k].id] = min(hits[k].groups.values())
        hooks.append(current)
    for hook in hooks:
        hook.hit_ids.sort()
    return hooks
