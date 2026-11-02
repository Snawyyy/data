"""SQLite storage for artists, follower snapshots, and tweets.

Records passed in are plain dicts keyed by column name, so the storage does
not depend on which API the data came from.
"""

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (
    id              TEXT PRIMARY KEY,
    username        TEXT NOT NULL,
    name            TEXT,
    bio             TEXT,
    location        TEXT,
    url             TEXT,
    created_at      TEXT,
    verified        INTEGER,
    followers       INTEGER,
    following       INTEGER,
    tweet_count     INTEGER,
    listed_count    INTEGER,
    profile_image   TEXT,
    -- native: grew on X; migrated: brought an audience from elsewhere
    origin          TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (origin IN ('native', 'migrated', 'unknown')),
    origin_notes    TEXT,
    source          TEXT,
    found_via       TEXT,
    -- when this artist's following list and reposts were last crawled
    expanded_at     TEXT,
    added_at        TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artists_username ON artists (lower(username));

CREATE TABLE IF NOT EXISTS snapshots (
    artist_id       TEXT NOT NULL REFERENCES artists (id),
    taken_at        TEXT NOT NULL,
    followers       INTEGER,
    following       INTEGER,
    tweet_count     INTEGER,
    listed_count    INTEGER,
    -- live: fetched now; wayback: read from an archived profile page
    source          TEXT NOT NULL DEFAULT 'live',
    PRIMARY KEY (artist_id, taken_at)
);

CREATE TABLE IF NOT EXISTS tweets (
    id              TEXT PRIMARY KEY,
    artist_id       TEXT NOT NULL REFERENCES artists (id),
    created_at      TEXT,
    text            TEXT,
    like_count      INTEGER,
    retweet_count   INTEGER,
    reply_count     INTEGER,
    quote_count     INTEGER,
    impression_count INTEGER,
    has_media       INTEGER,
    is_reply        INTEGER,
    is_retweet      INTEGER,
    is_quote        INTEGER,
    fetched_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tweets_artist_time ON tweets (artist_id, created_at);

-- detected blow-up windows, one row per artist per method
CREATE TABLE IF NOT EXISTS blowups (
    artist_id       TEXT NOT NULL REFERENCES artists (id),
    method          TEXT NOT NULL CHECK (method IN ('followers', 'engagement')),
    start_at        TEXT,
    end_at          TEXT,
    before_value    REAL,
    after_value     REAL,
    multiplier      REAL,
    trigger_tweet_id TEXT,
    trigger_ratio   REAL,
    -- data starts too late to be sure this was the real take-off
    cut_off         INTEGER NOT NULL DEFAULT 0,
    -- follower window too long to pinpoint anything
    coarse          INTEGER NOT NULL DEFAULT 0,
    computed_at     TEXT NOT NULL,
    PRIMARY KEY (artist_id, method)
);

-- handles known only by name (e.g. from Wikidata), waiting to be looked up
CREATE TABLE IF NOT EXISTS candidates (
    username        TEXT PRIMARY KEY COLLATE NOCASE,
    source          TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'rejected', 'missing')),
    added_at        TEXT NOT NULL,
    checked_at      TEXT
);
"""

# columns added after the first schema, for databases made before them
LATE_COLUMNS = {
    "artists": ("found_via TEXT", "expanded_at TEXT"),
    "snapshots": ("source TEXT NOT NULL DEFAULT 'live'",),
    "blowups": (
        "cut_off INTEGER NOT NULL DEFAULT 0",
        "coarse INTEGER NOT NULL DEFAULT 0",
    ),
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def migrate(conn):
    """Add columns that a database made by an older version is missing."""
    for table, cols in LATE_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col.split()[0] not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")


ARTIST_FIELDS = (
    "id", "username", "name", "bio", "location", "url", "created_at",
    "verified", "followers", "following", "tweet_count", "listed_count",
    "profile_image",
)
TWEET_FIELDS = (
    "id", "created_at", "text", "like_count", "retweet_count", "reply_count",
    "quote_count", "impression_count", "has_media", "is_reply", "is_retweet",
    "is_quote",
)


def upsert_artist(conn, artist, source, found_via=None):
    """Insert or refresh an artist record and log a follower snapshot."""
    ts = now_iso()
    values = [artist.get(f) for f in ARTIST_FIELDS]
    cols = ", ".join(ARTIST_FIELDS)
    marks = ", ".join("?" * len(ARTIST_FIELDS))
    updates = ", ".join(
        f"{f} = excluded.{f}" for f in ARTIST_FIELDS if f not in ("id", "created_at")
    )
    conn.execute(
        f"""
        INSERT INTO artists ({cols}, source, found_via, added_at, updated_at)
        VALUES ({marks}, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET {updates}, updated_at = excluded.updated_at
        """,
        values + [source, found_via, ts, ts],
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO snapshots
            (artist_id, taken_at, followers, following, tweet_count, listed_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            artist["id"], ts, artist.get("followers"), artist.get("following"),
            artist.get("tweet_count"), artist.get("listed_count"),
        ),
    )


def upsert_tweet(conn, artist_id, tweet):
    cols = ", ".join(TWEET_FIELDS)
    marks = ", ".join("?" * len(TWEET_FIELDS))
    conn.execute(
        f"""
        INSERT OR REPLACE INTO tweets ({cols}, artist_id, fetched_at)
        VALUES ({marks}, ?, ?)
        """,
        [tweet.get(f) for f in TWEET_FIELDS] + [artist_id, now_iso()],
    )


def tweet_exists(conn, tweet_id):
    row = conn.execute("SELECT 1 FROM tweets WHERE id = ?", (tweet_id,)).fetchone()
    return row is not None


def artist_ids(conn):
    return {r["id"] for r in conn.execute("SELECT id FROM artists")}


def next_unexpanded(conn):
    """Biggest artist whose network has not been crawled yet."""
    return conn.execute(
        "SELECT id, username FROM artists WHERE expanded_at IS NULL "
        "ORDER BY followers DESC LIMIT 1"
    ).fetchone()


def mark_expanded(conn, artist_id):
    conn.execute(
        "UPDATE artists SET expanded_at = ? WHERE id = ?", (now_iso(), artist_id)
    )


def add_candidates(conn, usernames, source):
    ts = now_iso()
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO candidates (username, source, added_at) VALUES (?, ?, ?)",
        [(u, source, ts) for u in usernames],
    )
    return conn.total_changes - before


def pending_candidates(conn, n):
    return [
        r["username"]
        for r in conn.execute(
            "SELECT username FROM candidates WHERE status = 'pending' LIMIT ?", (n,)
        )
    ]


def has_candidates(conn):
    return conn.execute("SELECT 1 FROM candidates LIMIT 1").fetchone() is not None


def set_candidate_status(conn, username, status):
    conn.execute(
        "UPDATE candidates SET status = ?, checked_at = ? WHERE username = ?",
        (status, now_iso(), username),
    )


def add_wayback_snapshot(conn, artist_id, taken_at, followers):
    conn.execute(
        "INSERT OR REPLACE INTO snapshots (artist_id, taken_at, followers, source) "
        "VALUES (?, ?, ?, 'wayback')",
        (artist_id, taken_at, followers),
    )


def follower_history(conn, artist_id):
    """(taken_at, followers) pairs from all sources, oldest first."""
    return [
        (r["taken_at"], r["followers"])
        for r in conn.execute(
            "SELECT taken_at, followers FROM snapshots "
            "WHERE artist_id = ? AND followers IS NOT NULL ORDER BY taken_at",
            (artist_id,),
        )
    ]


def joined_at(conn, artist_id):
    row = conn.execute(
        "SELECT created_at FROM artists WHERE id = ?", (artist_id,)
    ).fetchone()
    return row["created_at"] if row else None


def own_posts(conn, artist_id):
    """(created_at, likes, tweet_id) for original posts, oldest first."""
    return [
        (r["created_at"], r["like_count"], r["id"])
        for r in conn.execute(
            "SELECT id, created_at, like_count FROM tweets WHERE artist_id = ? "
            "AND is_reply = 0 AND is_retweet = 0 AND like_count IS NOT NULL "
            "ORDER BY created_at",
            (artist_id,),
        )
    ]


def save_blowup(conn, artist_id, method, result):
    conn.execute(
        """
        INSERT OR REPLACE INTO blowups (artist_id, method, start_at, end_at,
            before_value, after_value, multiplier, trigger_tweet_id,
            trigger_ratio, cut_off, coarse, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artist_id, method, result["start_at"], result["end_at"],
            result["before"], result["after"], result["multiplier"],
            result.get("trigger_tweet_id"), result.get("trigger_ratio"),
            int(result.get("cut_off", False)), int(result.get("coarse", False)),
            now_iso(),
        ),
    )


def report_artists(conn, min_followers):
    return conn.execute(
        "SELECT id, username, followers, created_at FROM artists WHERE followers >= ?",
        (min_followers,),
    ).fetchall()


def blowups_for(conn, artist_id):
    """Saved blow-ups for one artist, keyed by method, in detector field names."""
    results = {}
    for r in conn.execute("SELECT * FROM blowups WHERE artist_id = ?", (artist_id,)):
        results[r["method"]] = {
            "start_at": r["start_at"], "end_at": r["end_at"],
            "before": r["before_value"], "after": r["after_value"],
            "multiplier": r["multiplier"],
            "trigger_tweet_id": r["trigger_tweet_id"],
            "trigger_ratio": r["trigger_ratio"],
            "cut_off": bool(r["cut_off"]), "coarse": bool(r["coarse"]),
        }
    return results
