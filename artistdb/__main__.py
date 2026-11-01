"""Command line entry point: python -m artistdb <command>."""

import argparse
import re
import sys

from . import db
from .fxapi import FxApiError, FxClient, artist_record, tweet_record

DEFAULT_DB = "artists.db"
ART_BIO = (
    r"\bart(ist|work|s)?\b|illustrat|\bdraw|paint|sketch|animator|"
    r"comic artist|mangaka|concept art|commission|character design|"
    r"\u7d75\u5e2b|\u30a4\u30e9\u30b9\u30c8|\u6f2b\u753b\u5bb6"
)
KNOWN_STREAK_STOP = 5  # stop a timeline after this many already stored tweets


def read_usernames(path):
    names = []
    with open(path) as f:
        for line in f:
            name = line.split(",")[0].strip().lstrip("@")
            if name and not name.startswith("#") and name.lower() != "username":
                names.append(name)
    return names


def cmd_seed(conn, client, args):
    names = read_usernames(args.file)
    count = 0
    for name in names:
        artist = client.user(name)
        if artist:
            db.upsert_artist(conn, artist, source="seed")
            conn.commit()
            count += 1
    print(f"seeded {count} of {len(names)} usernames")


def cmd_refresh(conn, client, args):
    names = [r["username"] for r in conn.execute("SELECT username FROM artists")]
    count = 0
    for name in names:
        artist = client.user(name)
        if artist:
            db.upsert_artist(conn, artist, source="seed")
            conn.commit()
            count += 1
    print(f"refreshed {count} of {len(names)} profiles")


def is_own_post(status, artist_id):
    is_repost = bool(status.get("reposted_by"))
    author_id = (status.get("author") or {}).get("id")
    return not is_repost and author_id == artist_id


def cmd_tweets(conn, client, args):
    rows = conn.execute(
        "SELECT id, username FROM artists WHERE followers >= ? "
        "ORDER BY followers DESC",
        (args.min_followers,),
    ).fetchall()
    for row in rows:
        n = 0
        known_streak = 0
        try:
            for status in client.statuses(row["username"], limit=args.limit):
                if not is_own_post(status, row["id"]):
                    continue
                if not args.full and db.tweet_exists(conn, status["id"]):
                    known_streak += 1
                    if known_streak >= KNOWN_STREAK_STOP:
                        break
                    continue
                known_streak = 0
                db.upsert_tweet(conn, row["id"], tweet_record(status))
                n += 1
        except FxApiError as e:
            print(f"@{row['username']}: stopped early, {e}", file=sys.stderr)
        conn.commit()
        print(f"@{row['username']}: {n} new tweets")


def shared_authors(status):
    """Authors an artist amplified: the original author of a repost and the
    author of a quoted post."""
    if status.get("reposted_by") and status.get("author"):
        yield status["author"]
    quote = status.get("quote") or {}
    if quote.get("author"):
        yield quote["author"]


def cmd_discover(conn, client, args):
    """Snowball: look through known artists' timelines for artists they
    repost or quote, and add the ones that look like big artists."""
    bio_re = re.compile(args.bio_match, re.IGNORECASE)
    known = {r["id"] for r in conn.execute("SELECT id FROM artists")}
    sources = conn.execute(
        "SELECT username FROM artists WHERE followers >= ? ORDER BY random()",
        (args.from_min_followers,),
    ).fetchall()
    added = 0
    for row in sources[: args.max_sources]:
        try:
            for status in client.statuses(row["username"], limit=args.limit):
                for user in shared_authors(status):
                    if user["id"] in known:
                        continue
                    known.add(user["id"])
                    is_big = (user.get("followers") or 0) >= args.min_followers
                    is_artist = bool(bio_re.search(user.get("description") or ""))
                    if is_big and is_artist:
                        db.upsert_artist(conn, artist_record(user), source="repost")
                        added += 1
                        print(f"+ @{user['screen_name']} ({user['followers']}) via @{row['username']}")
        except FxApiError as e:
            print(f"@{row['username']}: stopped early, {e}", file=sys.stderr)
        conn.commit()
    print(f"added {added} artists from {min(len(sources), args.max_sources)} timelines")


def cmd_origin(conn, client, args):
    cur = conn.execute(
        "UPDATE artists SET origin = ?, origin_notes = ? "
        "WHERE lower(username) = lower(?)",
        (args.origin, args.notes, args.username.lstrip("@")),
    )
    conn.commit()
    print("updated" if cur.rowcount else f"no artist @{args.username}")


def cmd_stats(conn, client, args):
    q = lambda sql: conn.execute(sql).fetchone()[0]
    print(f"artists:   {q('SELECT count(*) FROM artists')}")
    print(f"snapshots: {q('SELECT count(*) FROM snapshots')}")
    print(f"tweets:    {q('SELECT count(*) FROM tweets')}")
    for r in conn.execute("SELECT origin, count(*) n FROM artists GROUP BY origin"):
        print(f"  {r['origin']}: {r['n']}")


def build_parser():
    p = argparse.ArgumentParser(prog="artistdb")
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="import usernames from a text or CSV file")
    s.add_argument("file")
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("discover", help="find artists that known artists repost or quote")
    s.add_argument("--min-followers", type=int, default=10000,
                   help="only add artists at least this big")
    s.add_argument("--from-min-followers", type=int, default=0,
                   help="only scan timelines of artists at least this big")
    s.add_argument("--max-sources", type=int, default=50,
                   help="timelines to scan per run")
    s.add_argument("--limit", type=int, default=100, help="posts to scan per timeline")
    s.add_argument("--bio-match", default=ART_BIO,
                   help="regex a bio must match to count as an artist")
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("refresh", help="re-fetch all profiles and log snapshots")
    s.set_defaults(func=cmd_refresh)

    s = sub.add_parser("tweets", help="fetch timelines for stored artists")
    s.add_argument("--min-followers", type=int, default=0)
    s.add_argument("--limit", type=int, default=1000, help="max posts to scan per artist")
    s.add_argument("--full", action="store_true", help="do not stop at stored tweets")
    s.set_defaults(func=cmd_tweets)

    s = sub.add_parser("origin", help="mark an artist native or migrated")
    s.add_argument("username")
    s.add_argument("origin", choices=["native", "migrated", "unknown"])
    s.add_argument("--notes")
    s.set_defaults(func=cmd_origin)

    s = sub.add_parser("stats", help="show database counts")
    s.set_defaults(func=cmd_stats)
    return p


def main():
    args = build_parser().parse_args()
    conn = db.connect(args.db)
    try:
        args.func(conn, FxClient(), args)
    except KeyboardInterrupt:
        conn.commit()
        sys.exit("interrupted, progress saved")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
