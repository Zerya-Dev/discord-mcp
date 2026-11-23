"""Facets over all hooks of a query (not just the shown page). Hooks only come from readable channels."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from zerya_discord_mcp.index.models import snowflake_time
from zerya_discord_mcp.query.hooks import Hook


@dataclass(frozen=True, slots=True)
class Facets:
    channels: list[tuple[int, int]]  # channel id, hooks
    authors: list[tuple[int, int]]  # user id, focal messages
    months: list[tuple[str, int]]  # YYYY-MM, hooks


def compute_facets(conn: sqlite3.Connection, hooks: list[Hook], tz: ZoneInfo, *, top: int = 5) -> Facets:
    channels = Counter(h.channel_id for h in hooks)
    months = Counter(snowflake_time(h.first_id).astimezone(tz).strftime("%Y-%m") for h in hooks)
    focal = [mid for h in hooks for mid in h.hit_ids]
    authors: Counter[int] = Counter()
    if focal:
        for author_id, count in conn.execute(
            "SELECT author_id, count(*) FROM messages WHERE id IN (SELECT value FROM json_each(?)) GROUP BY author_id",
            (json.dumps(focal),),
        ):
            authors[int(author_id)] = int(count)
    return Facets(
        channels=channels.most_common(top),
        authors=authors.most_common(top),
        months=sorted(months.most_common(top + 1), reverse=True),
    )
