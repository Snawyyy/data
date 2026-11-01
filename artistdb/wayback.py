"""Follower history from archived X/Twitter profile pages on the Wayback Machine.

Pages archived before about 2020 embed the follower count. Later archives are
mostly empty app shells, so recent years have fewer points.
"""

import gzip
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CDX = "https://web.archive.org/cdx/search/cdx"
RAW = "https://web.archive.org/web/{ts}id_/{url}"
USER_AGENT = "artistdb/0.1 (research crawler)"
DELAY = 1.5  # archive.org throttles fast clients
LEGACY_COUNT = re.compile(
    r"ProfileNav-item--followers.{0,800}?data-count=\"?(\d+)", re.DOTALL
)


def _fetch(url, timeout=60):
    time.sleep(DELAY)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    # raw (id_) archives keep the original encoding, often gzip
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body.decode("utf-8", errors="replace")


def snapshot_list(username):
    """(timestamp, original_url) of archived profile pages, one per month."""
    rows = []
    for host in ("twitter.com", "x.com"):
        params = {
            "url": f"{host}/{username}",
            "output": "json",
            "filter": "statuscode:200",
            "collapse": "timestamp:6",
            "fl": "timestamp,original",
        }
        try:
            data = json.loads(_fetch(CDX + "?" + urllib.parse.urlencode(params)) or "[]")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"wayback list @{username} on {host}: {e}", file=sys.stderr)
            continue
        rows.extend((ts, url) for ts, url in data[1:])
    return sorted(set(rows))


def parse_followers(page, username):
    """Follower count of `username` from an archived profile page, or None."""
    text = html.unescape(page)
    owner = re.compile(
        r'"screen_name"\s*:\s*"' + re.escape(username) + r'".{0,4000}?'
        r'"followers_count"\s*:\s*(\d+)',
        re.IGNORECASE | re.DOTALL,
    )
    for pattern in (owner, LEGACY_COUNT):
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    return None


def iso_from_ts(ts):
    dt = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return dt.isoformat()


def follower_history(username, max_pages=40):
    """Yield (iso_time, followers) read from archived pages, oldest first.
    Fetches at most `max_pages` pages, spread evenly over the archive."""
    snaps = snapshot_list(username)
    if len(snaps) > max_pages:
        step = len(snaps) / max_pages
        snaps = [snaps[int(i * step)] for i in range(max_pages)]
    for ts, url in snaps:
        try:
            page = _fetch(RAW.format(ts=ts, url=url))
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"wayback {ts} @{username}: {e}", file=sys.stderr)
            continue
        count = parse_followers(page, username)
        if count is not None:
            yield iso_from_ts(ts), count
