"""Client for the public FxTwitter API (api.fxtwitter.com), no token needed.

Responses are turned into plain records matching the database columns.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.fxtwitter.com"
USER_AGENT = "artistdb/0.1 (research)"
RETRIES = 4
DELAY = 0.5  # seconds between requests, to stay polite


class FxApiError(Exception):
    pass


def _iso_from_joined(joined):
    # "Tue Mar 21 20:50:14 +0000 2006"
    if not joined:
        return None
    dt = datetime.strptime(joined, "%a %b %d %H:%M:%S %z %Y")
    return dt.astimezone(timezone.utc).isoformat()


def _iso_from_ts(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def artist_record(user):
    """Map an FxTwitter user or author object to an artists row."""
    return {
        "id": user["id"],
        "username": user["screen_name"],
        "name": user.get("name"),
        "bio": user.get("description"),
        "location": user.get("location") or None,
        "url": (user.get("website") or {}).get("url"),
        "created_at": _iso_from_joined(user.get("joined")),
        "verified": int(bool((user.get("verification") or {}).get("verified"))),
        "followers": user.get("followers"),
        "following": user.get("following"),
        # profile lookups call it "tweets", status authors call it "statuses"
        "tweet_count": user.get("tweets", user.get("statuses")),
        "listed_count": None,
        "profile_image": user.get("avatar_url"),
    }


def tweet_record(status):
    """Map an FxTwitter status object to a tweets row."""
    media = (status.get("media") or {}).get("all") or []
    return {
        "id": status["id"],
        "created_at": _iso_from_ts(status.get("created_timestamp")),
        "text": status.get("text"),
        "like_count": status.get("likes"),
        "retweet_count": status.get("reposts"),
        "reply_count": status.get("replies"),
        "quote_count": status.get("quotes"),
        "impression_count": status.get("views"),
        "has_media": int(bool(media)),
        "is_reply": int(bool(status.get("replying_to"))),
        "is_retweet": 0,
        "is_quote": int(bool(status.get("quote"))),
    }


class FxClient:
    def _get(self, path, params=None):
        """GET a JSON endpoint. FxTwitter returns spurious 404s now and then,
        so 404, 429, and 5xx are retried with backoff."""
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        last = None
        for attempt in range(RETRIES):
            time.sleep(DELAY * (2 ** attempt))
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                last = e.code
                is_retryable = e.code in (404, 429) or e.code >= 500
                if not is_retryable:
                    raise FxApiError(f"{e.code} on {path}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
        raise FxApiError(f"{last} on {path} after {RETRIES} tries")

    def user(self, username):
        """Return an artist record, or None if the user can't be found."""
        try:
            data = self._get("/" + urllib.parse.quote(username))
        except FxApiError as e:
            print(f"skip @{username}: {e}", file=sys.stderr)
            return None
        user = data.get("user")
        return artist_record(user) if user else None

    def statuses(self, username, limit=200):
        """Yield raw status objects from a user's timeline, newest first.
        Reposts show up with a "reposted_by" key and the original author."""
        path = f"/2/profile/{urllib.parse.quote(username)}/statuses"
        params = {}
        got = 0
        while got < limit:
            data = self._get(path, params)
            results = data.get("results") or []
            yield from results
            got += len(results)
            cursor = (data.get("cursor") or {}).get("bottom")
            if not cursor or not results:
                return
            params["cursor"] = cursor
