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
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


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


def upsert_artist(conn, artist, source):
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
        INSERT INTO artists ({cols}, source, added_at, updated_at)
        VALUES ({marks}, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET {updates}, updated_at = excluded.updated_at
        """,
        values + [source, ts, ts],
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
