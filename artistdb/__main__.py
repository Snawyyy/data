"""Command line entry point: python -m artistdb <command>."""

import argparse
import re
import sys

from . import blowup, db, wayback, wikidata
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


class Scanner:
    """Crawls outward from known artists to find new ones.

    Each step either expands one artist (reads who they follow and whom they
    repost or quote) or checks one queued handle from Wikidata. The two
    alternate so the crawl does not stay inside one artist's circle. When the
    queue has never been filled, it is filled from Wikidata.
    """

    def __init__(self, conn, client, args):
        self.conn = conn
        self.client = client
        self.args = args
        self.bio_re = re.compile(args.bio_match, re.IGNORECASE)
        self.known = db.artist_ids(conn)
        self.added = 0

    def looks_like_artist(self, user):
        is_big = (user.get("followers") or 0) >= self.args.min_followers
        is_public = not user.get("protected")
        has_art_bio = bool(self.bio_re.search(user.get("description") or ""))
        return is_big and is_public and has_art_bio

    def accept(self, user, source, via):
        if user["id"] in self.known or not self.looks_like_artist(user):
            return
        self.known.add(user["id"])
        db.upsert_artist(self.conn, artist_record(user), source=source, found_via=via)
        self.added += 1
        print(f"+ @{user['screen_name']} ({user['followers']}) {source} of @{via}")

    def expand(self, row):
        name = row["username"]
        try:
            for user in self.client.following(name, limit=self.args.following_limit):
                self.accept(user, "following", name)
            for status in self.client.statuses(name, limit=self.args.posts_limit):
                for user in shared_authors(status):
                    self.accept(user, "repost", name)
        except FxApiError as e:
            print(f"@{name}: stopped early, {e}", file=sys.stderr)
        db.mark_expanded(self.conn, row["id"])

    def check_candidate(self, name):
        artist = self.client.user(name)
        if artist is None:
            db.set_candidate_status(self.conn, name, "missing")
            return
        user = {
            "id": artist["id"], "screen_name": artist["username"],
            "followers": artist["followers"], "description": artist["bio"],
        }
        if not self.looks_like_artist(user):
            db.set_candidate_status(self.conn, name, "rejected")
            return
        db.upsert_artist(self.conn, artist, source="wikidata")
        db.set_candidate_status(self.conn, name, "accepted")
        if artist["id"] not in self.known:
            self.known.add(artist["id"])
            self.added += 1
            print(f"+ @{artist['username']} ({artist['followers']}) wikidata")

    def bootstrap(self):
        print("filling queue from Wikidata...")
        n = db.add_candidates(self.conn, wikidata.artist_handles(), "wikidata")
        print(f"queued {n} handles")

    def step(self, turn):
        """Do one unit of work. Returns False when nothing is left."""
        row = db.next_unexpanded(self.conn)
        pending = db.pending_candidates(self.conn, 1)
        prefer_queue = turn % 2 == 1
        if pending and (prefer_queue or row is None):
            self.check_candidate(pending[0])
        elif row is not None:
            self.expand(row)
        elif not db.has_candidates(self.conn):
            self.bootstrap()
        else:
            return False
        self.conn.commit()
        return True

    def run(self):
        turn = 0
        while self.args.max_artists == 0 or len(self.known) < self.args.max_artists:
            if self.args.max_steps and turn >= self.args.max_steps:
                break
            if not self.step(turn):
                print("nothing left to scan")
                break
            turn += 1
        print(f"added {self.added} artists, {len(self.known)} total")


def cmd_scan(conn, client, args):
    Scanner(conn, client, args).run()


def artists_to_process(conn, args):
    """Artists picked by --user or by --min-followers, biggest first."""
    if args.user:
        return conn.execute(
            "SELECT id, username FROM artists WHERE lower(username) = lower(?)",
            (args.user.lstrip("@"),),
        ).fetchall()
    return conn.execute(
        "SELECT id, username FROM artists WHERE followers >= ? ORDER BY followers DESC",
        (args.min_followers,),
    ).fetchall()


def has_wayback(conn, artist_id):
    row = conn.execute(
        "SELECT 1 FROM snapshots WHERE artist_id = ? AND source = 'wayback' LIMIT 1",
        (artist_id,),
    ).fetchone()
    return row is not None


def cmd_history(conn, client, args):
    for row in artists_to_process(conn, args):
        if has_wayback(conn, row["id"]) and not args.redo:
            continue
        n = 0
        for taken_at, followers in wayback.follower_history(
            row["username"], max_pages=args.max_pages
        ):
            db.add_wayback_snapshot(conn, row["id"], taken_at, followers)
            n += 1
        conn.commit()
        print(f"@{row['username']}: {n} archived follower counts")


def _fmt(n):
    if n is None:
        return "?"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else f"{n:.0f}"


def cmd_blowup(conn, client, args):
    for row in artists_to_process(conn, args):
        fol = blowup.from_followers(db.follower_history(conn, row["id"]))
        eng = blowup.from_engagement(db.own_posts(conn, row["id"]))
        conn.execute("DELETE FROM blowups WHERE artist_id = ?", (row["id"],))
        parts = []
        if fol:
            db.save_blowup(conn, row["id"], "followers", fol)
            parts.append(
                f"followers {_fmt(fol['before'])} -> {_fmt(fol['after'])} "
                f"between {fol['start_at'][:7]} and {fol['end_at'][:7]}"
            )
        if eng:
            db.save_blowup(conn, row["id"], "engagement", eng)
            url = f"https://x.com/{row['username']}/status/{eng['trigger_tweet_id']}"
            parts.append(
                f"likes {_fmt(eng['before'])} -> {_fmt(eng['after'])} from "
                f"{eng['start_at']}, trigger {url} ({eng['trigger_ratio']}x)"
            )
        if parts or args.user:
            print(f"@{row['username']}: " + ("; ".join(parts) or "no clear blow-up"))
    conn.commit()


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

    s = sub.add_parser("scan", help="crawl X for artists, starting from Wikidata")
    s.add_argument("--min-followers", type=int, default=10000,
                   help="only keep artists at least this big")
    s.add_argument("--max-artists", type=int, default=0,
                   help="stop at this many artists (0 = no limit)")
    s.add_argument("--max-steps", type=int, default=0,
                   help="stop after this many steps (0 = no limit)")
    s.add_argument("--following-limit", type=int, default=400,
                   help="accounts to read from each following list")
    s.add_argument("--posts-limit", type=int, default=100,
                   help="posts to scan per artist for reposts and quotes")
    s.add_argument("--bio-match", default=ART_BIO,
                   help="regex a bio must match to count as an artist")
    s.set_defaults(func=cmd_scan)

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

    s = sub.add_parser("history", help="read old follower counts from the Wayback Machine")
    s.add_argument("--user", help="only this artist")
    s.add_argument("--min-followers", type=int, default=0)
    s.add_argument("--max-pages", type=int, default=40,
                   help="archived pages to read per artist")
    s.add_argument("--redo", action="store_true", help="refetch artists already done")
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("blowup", help="find when each artist blew up")
    s.add_argument("--user", help="only this artist")
    s.add_argument("--min-followers", type=int, default=0)
    s.set_defaults(func=cmd_blowup)

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
