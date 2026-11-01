"""Command line entry point: python -m artistdb <command>."""

import argparse
import os
import sys

from . import db
from .xapi import XApiError, XClient

DEFAULT_DB = "artists.db"
DEFAULT_QUERY = (
    "(#digitalart OR #illustration OR #art OR #artist OR #drawing "
    "OR #animation OR #conceptart) has:media -is:retweet"
)


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
    for user in client.users_by_username(names):
        db.upsert_artist(conn, user, source="seed")
        count += 1
    conn.commit()
    print(f"seeded {count} of {len(names)} usernames")


def cmd_discover(conn, client, args):
    count = 0
    for user in client.search_recent_authors(args.query, limit=args.limit):
        followers = user.get("public_metrics", {}).get("followers_count", 0)
        if followers < args.min_followers:
            continue
        db.upsert_artist(conn, user, source="search")
        count += 1
    conn.commit()
    print(f"added or refreshed {count} artists with >= {args.min_followers} followers")


def cmd_refresh(conn, client, args):
    names = [r["username"] for r in conn.execute("SELECT username FROM artists")]
    for user in client.users_by_username(names):
        db.upsert_artist(conn, user, source="seed")
    conn.commit()
    print(f"refreshed {len(names)} profiles")


def cmd_tweets(conn, client, args):
    rows = conn.execute(
        "SELECT id, username FROM artists WHERE followers >= ? "
        "ORDER BY followers DESC",
        (args.min_followers,),
    ).fetchall()
    for row in rows:
        since = None if args.full else db.newest_tweet_id(conn, row["id"])
        n = 0
        for tweet in client.user_tweets(row["id"], since_id=since, limit=args.limit):
            db.upsert_tweet(conn, row["id"], tweet)
            n += 1
        conn.commit()
        print(f"@{row['username']}: {n} tweets")


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
    s.set_defaults(func=cmd_seed, needs_api=True)

    s = sub.add_parser("discover", help="find artists via recent hashtag search")
    s.add_argument("--query", default=DEFAULT_QUERY)
    s.add_argument("--limit", type=int, default=500, help="tweets to scan")
    s.add_argument("--min-followers", type=int, default=10000)
    s.set_defaults(func=cmd_discover, needs_api=True)

    s = sub.add_parser("refresh", help="re-fetch all profiles and log snapshots")
    s.set_defaults(func=cmd_refresh, needs_api=True)

    s = sub.add_parser("tweets", help="fetch timelines for stored artists")
    s.add_argument("--min-followers", type=int, default=0)
    s.add_argument("--limit", type=int, default=3200, help="max tweets per artist")
    s.add_argument("--full", action="store_true", help="ignore already stored tweets")
    s.set_defaults(func=cmd_tweets, needs_api=True)

    s = sub.add_parser("origin", help="mark an artist native or migrated")
    s.add_argument("username")
    s.add_argument("origin", choices=["native", "migrated", "unknown"])
    s.add_argument("--notes")
    s.set_defaults(func=cmd_origin, needs_api=False)

    s = sub.add_parser("stats", help="show database counts")
    s.set_defaults(func=cmd_stats, needs_api=False)
    return p


def main():
    args = build_parser().parse_args()
    conn = db.connect(args.db)
    try:
        client = XClient(os.environ.get("X_BEARER_TOKEN")) if args.needs_api else None
        args.func(conn, client, args)
    except XApiError as e:
        sys.exit(f"error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
