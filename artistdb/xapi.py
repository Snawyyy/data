"""Minimal X API v2 client using only the standard library."""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.x.com/2"
USER_FIELDS = (
    "created_at,description,location,url,verified,public_metrics,"
    "profile_image_url"
)
TWEET_FIELDS = "created_at,public_metrics,attachments,referenced_tweets"
MAX_RETRIES = 5


class XApiError(Exception):
    pass


class XClient:
    def __init__(self, bearer_token):
        if not bearer_token:
            raise XApiError("X_BEARER_TOKEN is not set")
        self.token = bearer_token

    def _get(self, path, params):
        url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}"}
        )
        for _ in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    body = e.read().decode(errors="replace")
                    raise XApiError(f"{e.code} on {path}: {body}") from None
                reset = int(e.headers.get("x-rate-limit-reset", time.time() + 60))
                wait = max(reset - int(time.time()), 1) + 1
                print(f"rate limited, sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
        raise XApiError(f"gave up on {path} after {MAX_RETRIES} rate limits")

    def users_by_username(self, usernames):
        """Look up users, 100 per request. Yields user objects."""
        for i in range(0, len(usernames), 100):
            batch = usernames[i:i + 100]
            data = self._get(
                "/users/by",
                {"usernames": ",".join(batch), "user.fields": USER_FIELDS},
            )
            for err in data.get("errors", []):
                print(f"skip {err.get('value')}: {err.get('detail')}",
                      file=sys.stderr)
            yield from data.get("data", [])

    def user_tweets(self, user_id, since_id=None, limit=3200):
        """Yield a user's tweets, newest first. X caps this at ~3200."""
        params = {
            "max_results": 100,
            "tweet.fields": TWEET_FIELDS,
        }
        if since_id:
            params["since_id"] = since_id
        got = 0
        while got < limit:
            data = self._get(f"/users/{user_id}/tweets", params)
            tweets = data.get("data", [])
            yield from tweets
            got += len(tweets)
            token = data.get("meta", {}).get("next_token")
            if not token or not tweets:
                return
            params["pagination_token"] = token

    def search_recent_authors(self, query, limit=500):
        """Search the last 7 days and yield the distinct authors found."""
        params = {
            "query": query,
            "max_results": 100,
            "expansions": "author_id",
            "user.fields": USER_FIELDS,
        }
        seen = set()
        scanned = 0
        while scanned < limit:
            data = self._get("/tweets/search/recent", params)
            scanned += len(data.get("data", []))
            for user in data.get("includes", {}).get("users", []):
                if user["id"] not in seen:
                    seen.add(user["id"])
                    yield user
            token = data.get("meta", {}).get("next_token")
            if not token:
                return
            params["next_token"] = token
