"""Find when an artist blew up.

Two independent signals:
- followers: the stretch of follower history with the fastest relative growth.
- engagement: the first month where typical likes jump well above the
  artist's earlier level and stay there, plus the post most likely to have
  set it off.

Both return a dict with start_at, end_at, before, after, multiplier (and
trigger fields for engagement), or None when there is no clear blow-up.
"""

import math
from datetime import datetime
from statistics import median

MIN_BASE_FOLLOWERS = 500     # ignore growth from a near-empty account
MIN_GAP_DAYS = 20            # skip snapshot pairs too close to measure
MIN_FOLLOWER_MULT = 1.5      # a blow-up must at least add half again
WINDOW_KEEP = 0.5            # extend the window over segments this fast

BASELINE_MONTHS = 6
MIN_BASELINE_POSTS = 5
JUMP_RATIO = 5.0             # breakout month median vs baseline
SUSTAIN_MONTHS = 3
SUSTAIN_RATIO = 3.0          # following months must hold this level
TRIGGER_LOOKBACK_DAYS = 60


def _dt(iso):
    return datetime.fromisoformat(iso)


def from_followers(history):
    """history: [(iso_time, followers)] oldest first."""
    points = [(_dt(t), f) for t, f in history if f]
    segments = []
    for (t1, f1), (t2, f2) in zip(points, points[1:]):
        days = (t2 - t1).days
        if days < MIN_GAP_DAYS or f1 < MIN_BASE_FOLLOWERS or f2 <= f1:
            segments.append(None)
            continue
        segments.append(math.log(f2 / f1) / (days / 30.4))
    rates = [r for r in segments if r is not None]
    if not rates:
        return None

    best = segments.index(max(rates))
    keep = max(rates) * WINDOW_KEEP

    def is_fast(i):
        return 0 <= i < len(segments) and segments[i] is not None and segments[i] >= keep

    lo = hi = best
    while is_fast(lo - 1):
        lo -= 1
    while is_fast(hi + 1):
        hi += 1
    (t_start, f_start), (t_end, f_end) = points[lo], points[hi + 1]
    mult = f_end / f_start
    if mult < MIN_FOLLOWER_MULT:
        return None
    return {
        "start_at": t_start.isoformat(),
        "end_at": t_end.isoformat(),
        "before": f_start,
        "after": f_end,
        "multiplier": round(mult, 2),
    }


def _month(dt):
    return dt.year * 12 + dt.month - 1


def _month_iso(m):
    return f"{m // 12:04d}-{m % 12 + 1:02d}"


def from_engagement(posts):
    """posts: [(iso_time, likes, tweet_id)] original posts, oldest first."""
    rows = [(_dt(t), likes, tid) for t, likes, tid in posts if t]
    by_month = {}
    for dt, likes, tid in rows:
        by_month.setdefault(_month(dt), []).append((dt, likes, tid))
    if len(by_month) < BASELINE_MONTHS:
        return None

    def likes_in(first, last):
        return [l for m in range(first, last + 1) for _, l, _ in by_month.get(m, [])]

    for m in sorted(by_month):
        base_likes = likes_in(m - BASELINE_MONTHS, m - 1)
        month_likes = likes_in(m, m)
        later = likes_in(m + 1, m + SUSTAIN_MONTHS)
        has_data = (
            len(base_likes) >= MIN_BASELINE_POSTS and len(month_likes) >= 2 and later
        )
        if not has_data:
            continue
        baseline = max(median(base_likes), 1)
        jumped = median(month_likes) >= JUMP_RATIO * baseline
        held = median(later) >= SUSTAIN_RATIO * baseline
        if not (jumped and held):
            continue

        after = median(likes_in(m, m + SUSTAIN_MONTHS))
        month_end = max(dt for dt, _, _ in by_month[m])
        window = [
            r for r in rows
            if 0 <= (month_end - r[0]).days <= TRIGGER_LOOKBACK_DAYS
        ]
        trigger = max(window, key=lambda r: r[1])
        return {
            "start_at": _month_iso(m),
            "end_at": _month_iso(m + SUSTAIN_MONTHS),
            "before": baseline,
            "after": after,
            "multiplier": round(after / baseline, 2),
            "trigger_tweet_id": trigger[2],
            "trigger_ratio": round(trigger[1] / baseline, 1),
        }
    return None
