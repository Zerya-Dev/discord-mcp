"""Developer CLI over a local index file, as an admin who can read every indexed channel.

zdm-dev --db data/zdm.sqlite3 --guild-id 123 search "(fundacja|foundation) logo"
zdm-dev ... read "#general"
zdm-dev ... info
zdm-dev ... eval eval/questions.yaml
"""

import argparse
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from zerya_discord_mcp.access.grants import GrantStore
from zerya_discord_mcp.index.db import Database
from zerya_discord_mcp.index.vocab import Vocabulary
from zerya_discord_mcp.query.directory import Directory
from zerya_discord_mcp.query.info import Info
from zerya_discord_mcp.query.parser import QueryError
from zerya_discord_mcp.query.read import Reader
from zerya_discord_mcp.query.search import SearchEngine
from zerya_discord_mcp.testing.evaluation import load_cases, run_eval, summarize


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="zdm-dev")
    parser.add_argument("--db", default=os.environ.get("ZDM_DB_PATH", "data/zerya-discord-mcp.sqlite3"))
    parser.add_argument("--guild-id", type=int, default=int(os.environ.get("ZDM_GUILD_ID", "0")))
    parser.add_argument("--tz", default="Europe/Warsaw")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search")
    s.add_argument("q")
    s.add_argument("--context", type=int, default=5)
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--cursor")
    s.add_argument("--budget", type=int, default=4000)
    r = sub.add_parser("read")
    r.add_argument("target", nargs="?")
    r.add_argument("--before", type=int)
    r.add_argument("--after", type=int)
    r.add_argument("--since")
    r.add_argument("--cursor")
    i = sub.add_parser("info")
    i.add_argument("target", nargs="?")
    lc = sub.add_parser("login-code", help="create a one-time OAuth sign-in code for a member")
    lc.add_argument("member", help="username, nick or user ID")
    e = sub.add_parser("eval")
    e.add_argument("file", type=Path)
    e.add_argument("--k", type=int, default=10)
    e.add_argument("--context", type=int, default=5)
    args = parser.parse_args(argv)

    db = Database(args.db)
    db.open()
    tz = ZoneInfo(args.tz)
    conn = db.reader()
    allowed = frozenset(int(r[0]) for r in conn.execute("SELECT id FROM channels"))
    try:
        match args.command:
            case "search":
                vocab = Vocabulary()
                result = SearchEngine(vocab, tz, args.guild_id).search(
                    conn,
                    allowed,
                    args.q,
                    context=args.context,
                    limit=args.limit,
                    cursor=args.cursor,
                    budget_tokens=args.budget,
                )
                print(result.text)
            case "read":
                print(
                    Reader(tz, args.guild_id).read(
                        conn,
                        allowed,
                        args.target,
                        before=args.before,
                        after=args.after,
                        since=args.since,
                        cursor=args.cursor,
                    )
                )
            case "info":
                print(Info(tz, args.guild_id).info(conn, allowed, args.target))
            case "eval":
                results = run_eval(
                    conn, load_cases(args.file), allowed, guild_id=args.guild_id, k=args.k, context=args.context
                )
                print(summarize(results, args.k))
            case "login-code":
                member = Directory(conn, allowed).resolve_member(args.member)
                store = GrantStore(db, login_ttl=600)
                code, _ = store.create_login_code(member.id)
                print(f"Sign-in code for @{member.username}: {code} (valid 10 min, one use)")
            case _:
                parser.error("unknown command")
    except QueryError as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(2)
    finally:
        db.close()


if __name__ == "__main__":
    main()
