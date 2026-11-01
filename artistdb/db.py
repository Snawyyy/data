"""SQLite storage for artists, follower snapshots, and tweets."""

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


def upsert_artist(conn, user, source):
    """Insert or refresh an artist from an X API user object and log a snapshot."""
    m = user.get("public_metrics", {})
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO artists (id, username, name, bio, location, url, created_at,
            verified, followers, following, tweet_count, listed_count,
            profile_image, source, added_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            username = excluded.username, name = excluded.name,
            bio = excluded.bio, location = excluded.location,
            url = excluded.url, verified = excluded.verified,
            followers = excluded.followers, following = excluded.following,
            tweet_count = excluded.tweet_count,
            listed_count = excluded.listed_count,
            profile_image = excluded.profile_image,
            updated_at = excluded.updated_at
        """,
        (
            user["id"], user["username"], user.get("name"),
            user.get("description"), user.get("location"), user.get("url"),
            user.get("created_at"), int(bool(user.get("verified"))),
            m.get("followers_count"), m.get("following_count"),
            m.get("tweet_count"), m.get("listed_count"),
            user.get("profile_image_url"), source, ts, ts,
        ),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO snapshots
            (artist_id, taken_at, followers, following, tweet_count, listed_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"], ts, m.get("followers_count"), m.get("following_count"),
            m.get("tweet_count"), m.get("listed_count"),
        ),
    )


def upsert_tweet(conn, artist_id, tweet):
    m = tweet.get("public_metrics", {})
    refs = {r["type"] for r in tweet.get("referenced_tweets", [])}
    has_media = bool(tweet.get("attachments", {}).get("media_keys"))
    conn.execute(
        """
        INSERT OR REPLACE INTO tweets (id, artist_id, created_at, text,
            like_count, retweet_count, reply_count, quote_count,
            impression_count, has_media, is_reply, is_retweet, is_quote,
            fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tweet["id"], artist_id, tweet.get("created_at"), tweet.get("text"),
            m.get("like_count"), m.get("retweet_count"), m.get("reply_count"),
            m.get("quote_count"), m.get("impression_count"), int(has_media),
            int("replied_to" in refs), int("retweeted" in refs),
            int("quoted" in refs), now_iso(),
        ),
    )


def newest_tweet_id(conn, artist_id):
    row = conn.execute(
        "SELECT id FROM tweets WHERE artist_id = ? "
        "ORDER BY CAST(id AS INTEGER) DESC LIMIT 1",
        (artist_id,),
    ).fetchone()
    return row["id"] if row else None
