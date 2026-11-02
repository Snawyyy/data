"""Build a single-file HTML report of when each artist blew up.

Each artist gets two stacked charts on one shared time axis: followers and
monthly median likes. Everything from the API is escaped before it is written.
"""

import html
import json
import math
import urllib.parse
from datetime import datetime, timezone
from statistics import median

from . import db

WIDTH = 720
HEIGHT = 182
LEFT = 46
RIGHT = 14
TOP = 38
BOTTOM = 24
MAX_YEAR_LABELS = 7
MAX_LOG_TICKS = 5
LINE_GAP_DAYS = 183  # do not draw a likes line across a gap this long
EDGE_MARGIN = 150    # flip labels this close to the right edge

QUALITY_LABELS = {
    "reliable": "Reliable date",
    "flagged": "Unsure date",
    "none": "No signal",
}
QUALITY_ORDER = {"reliable": 0, "flagged": 1, "none": 2}


def classify(fol, eng):
    """reliable: at least one unflagged signal. flagged: signals, all flagged."""
    fol_ok = bool(fol) and not fol["cut_off"] and not fol["coarse"]
    eng_ok = bool(eng) and not eng["cut_off"]
    if fol_ok or eng_ok:
        return "reliable"
    return "flagged" if fol or eng else "none"


def best_date(fol, eng):
    """The most trustworthy date. Likes jumps give a month, so they win ties."""
    if eng and not eng["cut_off"]:
        return eng["start_at"][:7]
    if fol and not fol["cut_off"] and not fol["coarse"]:
        return fol["start_at"][:7]
    if eng:
        return eng["start_at"][:7]
    return fol["start_at"][:7] if fol else ""


def compact(n):
    if n is None:
        return "?"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e4:
        return f"{n / 1e3:.0f}K"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{n:.0f}"


def _axis_label(n):
    return f"{n // 1000000}M" if n >= 1000000 else f"{n // 1000}K" if n >= 1000 else str(n)


def _dt(iso):
    if len(iso) == 7:  # a month such as 2021-08
        iso += "-01"
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def monthly_median_likes(posts):
    """[(iso_month, median_likes)] from (iso_time, likes, id) posts."""
    by_month = {}
    for created_at, likes, _ in posts:
        if created_at:
            by_month.setdefault(created_at[:7], []).append(likes)
    return [(m, median(v)) for m, v in sorted(by_month.items())]


class Chart:
    """One log-scale time series as inline SVG."""

    def __init__(self, title, series, x0, x1, unit, break_gaps=False, dots=True):
        self.title = title
        self.points = [(_dt(t), v) for t, v in series]
        self.x0, self.x1 = x0, x1
        self.unit = unit
        self.break_gaps = break_gaps
        self.dots = dots
        values = [max(v, 1) for _, v in self.points]
        self.lo = math.floor(math.log10(min(values))) if values else 0
        self.hi = math.ceil(math.log10(max(values))) if values else 1
        self.hi = max(self.hi, self.lo + 1)

    def x(self, dt):
        span = (self.x1 - self.x0).total_seconds() or 1
        share = (dt - self.x0).total_seconds() / span
        return LEFT + share * (WIDTH - LEFT - RIGHT)

    def y(self, value):
        share = (math.log10(max(value, 1)) - self.lo) / (self.hi - self.lo)
        return HEIGHT - BOTTOM - share * (HEIGHT - TOP - BOTTOM)

    def _grid(self):
        parts = []
        step = max(1, math.ceil((self.hi - self.lo) / MAX_LOG_TICKS))
        for power in range(self.lo, self.hi + 1, step):
            y = self.y(10 ** power)
            parts.append(f'<line class="grid" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
            parts.append(
                f'<text class="tick" x="{LEFT - 6}" y="{y + 4:.1f}" text-anchor="end">'
                f"{_axis_label(10 ** power)}</text>"
            )
        years = range(self.x0.year + 1, self.x1.year + 1)
        step = max(1, math.ceil(len(years) / MAX_YEAR_LABELS))
        for year in list(years)[::step]:
            x = self.x(datetime(year, 1, 1, tzinfo=timezone.utc))
            parts.append(
                f'<text class="tick" x="{x:.1f}" y="{HEIGHT - 6}" text-anchor="middle">{year}</text>'
            )
        base = HEIGHT - BOTTOM
        parts.append(f'<line class="axis" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{base}" y2="{base}"/>')
        return parts

    def _window(self, start, end):
        x1 = max(self.x(start), LEFT)
        x2 = min(self.x(end), WIDTH - RIGHT)
        width = max(x2 - x1, 3)
        height = HEIGHT - TOP - BOTTOM
        near_edge = x1 > WIDTH - EDGE_MARGIN
        label_x, anchor = (x2, "end") if near_edge else (x1, "start")
        return [
            f'<rect class="window" x="{x1:.1f}" y="{TOP}" width="{width:.1f}" height="{height}"/>',
            f'<text class="note" x="{label_x:.1f}" y="{TOP - 5}" text-anchor="{anchor}">blow-up window</text>',
        ]

    def _segments(self):
        segments, current = [], []
        for dt, value in self.points:
            is_gap = current and (dt - current[-1][0]).days > LINE_GAP_DAYS
            if self.break_gaps and is_gap:
                segments.append(current)
                current = []
            current.append((dt, value))
        if current:
            segments.append(current)
        return segments

    def _lines(self):
        parts = []
        for segment in self._segments():
            is_lone = len(segment) == 1
            if not is_lone:
                coords = " ".join(f"{self.x(d):.1f},{self.y(v):.1f}" for d, v in segment)
                parts.append(f'<polyline class="line" points="{coords}"/>')
            if self.dots or is_lone:
                parts.extend(
                    f'<circle class="dot" cx="{self.x(d):.1f}" cy="{self.y(v):.1f}" r="4"/>'
                    for d, v in segment
                )
        return parts

    def _trigger(self, dt, likes):
        x, y = self.x(dt), self.y(likes)
        near_edge = x > WIDTH - EDGE_MARGIN
        label_x, anchor = (x - 9, "end") if near_edge else (x + 9, "start")
        return [
            f'<circle class="trigger" cx="{x:.1f}" cy="{y:.1f}" r="5"/>',
            f'<text class="label" x="{label_x:.1f}" y="{y + 4:.1f}" text-anchor="{anchor}">'
            f"likely trigger: {compact(likes)} likes</text>",
        ]

    def render(self, window=None, trigger=None):
        """window: (start_dt, end_dt). trigger: (dt, likes)."""
        parts = [f'<text class="title" x="{LEFT}" y="13">{html.escape(self.title)}</text>']
        parts.extend(self._grid())
        if window:
            parts.extend(self._window(*window))
        if self.points:
            parts.extend(self._lines())
        else:
            mid = (HEIGHT + TOP - BOTTOM) / 2
            parts.append(
                f'<text class="note" x="{(WIDTH + LEFT - RIGHT) / 2}" y="{mid}" '
                f'text-anchor="middle">no data</text>'
            )
        hover = [
            [round(self.x(d), 1), round(self.y(v), 1), d.strftime("%Y-%m-%d"),
             f"{compact(v)} {self.unit}"]
            for d, v in self.points
        ]
        if trigger:
            parts.extend(self._trigger(*trigger))
            d, likes = trigger
            hover.append([
                round(self.x(d), 1), round(self.y(likes), 1), d.strftime("%Y-%m-%d"),
                f"{compact(likes)} likes on the likely trigger post",
            ])
        hover.sort(key=lambda p: p[0])
        data = html.escape(json.dumps(hover), quote=True)
        summary = html.escape(f"{self.title}, {len(self.points)} points", quote=True)
        return (
            f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" tabindex="0" '
            f'aria-label="{summary}" data-points="{data}" '
            f'data-top="{TOP}" data-bottom="{HEIGHT - BOTTOM}">'
            + "".join(parts)
            + f'<line class="cross" x1="0" x2="0" y1="{TOP}" y2="{HEIGHT - BOTTOM}" visibility="hidden"/>'
            + '<circle class="focus" r="5" visibility="hidden"/></svg>'
        )


def _status_url(username, tweet_id):
    name = urllib.parse.quote(username)
    return html.escape(f"https://x.com/{name}/status/{urllib.parse.quote(str(tweet_id))}", quote=True)


def _flag_text(result):
    flags = []
    if result["cut_off"]:
        flags.append("data starts too late")
    if result.get("coarse"):
        flags.append("window too long")
    return f" ({', '.join(flags)})" if flags else ""


def _follower_text(fol):
    if not fol:
        return "No follower window."
    return (
        f"{compact(fol['before'])} to {compact(fol['after'])} followers, "
        f"{fol['start_at'][:7]} to {fol['end_at'][:7]}{_flag_text(fol)}."
    )


def _likes_text(eng):
    if not eng:
        return "No likes jump."
    return (
        f"Typical likes {compact(eng['before'])} to {compact(eng['after'])} "
        f"from {eng['start_at'][:7]}{_flag_text(eng)}."
    )


def _load(conn, row):
    """Everything the report needs for one artist."""
    results = db.blowups_for(conn, row["id"])
    fol, eng = results.get("followers"), results.get("engagement")
    posts = db.own_posts(conn, row["id"])
    likes_by_id = {tid: (t, likes) for t, likes, tid in posts}
    trigger = None
    if eng and eng["trigger_tweet_id"] in likes_by_id:
        t, likes = likes_by_id[eng["trigger_tweet_id"]]
        trigger = (_dt(t), likes)
    return {
        "username": row["username"],
        "followers": row["followers"],
        "joined": row["created_at"],
        "fol": fol,
        "eng": eng,
        "quality": classify(fol, eng),
        "date": best_date(fol, eng),
        "history": db.follower_history(conn, row["id"]),
        "likes": monthly_median_likes(posts),
        "trigger": trigger,
    }


def _time_range(artist, now):
    """From the account's creation to now, so missing early data is visible."""
    times = [_dt(t) for t, _ in artist["history"]] + [_dt(m) for m, _ in artist["likes"]]
    if artist["joined"]:
        times.append(_dt(artist["joined"]))
    start = min(times) if times else now
    return start, max(times + [now])


def _card(artist, now):
    x0, x1 = _time_range(artist, now)
    fol, eng = artist["fol"], artist["eng"]
    followers = Chart(
        "Followers (log scale, dots are recorded counts)", artist["history"],
        x0, x1, "followers",
    )
    likes = Chart(
        "Median likes per month (log scale)", artist["likes"], x0, x1,
        "median likes", break_gaps=True, dots=False,
    )
    fol_window = (_dt(fol["start_at"]), _dt(fol["end_at"])) if fol else None
    eng_window = (_dt(eng["start_at"]), _dt(eng["end_at"])) if eng else None

    name = html.escape(artist["username"])
    profile = html.escape(f"https://x.com/{urllib.parse.quote(artist['username'])}", quote=True)
    joined = html.escape((artist["joined"] or "unknown")[:7])
    links = ""
    if eng:
        url = _status_url(artist["username"], eng["trigger_tweet_id"])
        links = (
            f' <a href="{url}" rel="noreferrer">Likely trigger post</a> '
            f"({eng['trigger_ratio']:g}x their earlier typical likes)."
        )
    return (
        f'<section class="card" id="a-{name}" data-quality="{artist["quality"]}" '
        f'data-name="{name.lower()}">'
        f'<h3><a href="{profile}" rel="noreferrer">@{name}</a></h3>'
        f'<p class="meta">{compact(artist["followers"])} followers now, joined {joined}. '
        f'<span class="badge">{QUALITY_LABELS[artist["quality"]]}</span></p>'
        f'<p class="meta">{_follower_text(fol)} {_likes_text(eng)}{links}</p>'
        + followers.render(window=fol_window)
        + likes.render(window=eng_window, trigger=artist["trigger"])
        + "</section>"
    )


def _table_row(artist):
    fol, eng = artist["fol"], artist["eng"]
    name = html.escape(artist["username"])
    trigger = "-"
    ratio = 0
    if eng:
        url = _status_url(artist["username"], eng["trigger_tweet_id"])
        ratio = eng["trigger_ratio"]
        trigger = f'<a href="{url}" rel="noreferrer">{ratio:g}x</a>'
    rank = QUALITY_ORDER[artist["quality"]]
    return (
        f'<tr data-quality="{artist["quality"]}" data-name="{name.lower()}">'
        f'<td data-sort="{name.lower()}"><a href="#a-{name}">@{name}</a></td>'
        f'<td class="num" data-sort="{artist["followers"] or 0}">{compact(artist["followers"])}</td>'
        f'<td data-sort="{html.escape(artist["joined"] or "")}">{html.escape((artist["joined"] or "")[:7])}</td>'
        f'<td data-sort="{rank}">{QUALITY_LABELS[artist["quality"]]}</td>'
        f'<td data-sort="{artist["date"]}">{artist["date"] or "-"}</td>'
        f'<td data-sort="{fol["before"] if fol else -1}">{_follower_text(fol)}</td>'
        f'<td data-sort="{eng["before"] if eng else -1}">{_likes_text(eng)}</td>'
        f'<td class="num" data-sort="{ratio}">{trigger}</td></tr>'
    )


def _tile(label, value):
    return f'<div class="tile"><div class="value">{value}</div><div class="tile-label">{label}</div></div>'


STYLE = """
.viz-root { color-scheme: light; --surface: #fcfcfb; --text: #0b0b0b;
  --text-2: #52514e; --muted: #6f6e68; --grid: #e1e0d9; --axis: #c3c2b7;
  --ring: rgba(11,11,11,0.10); --wash: rgba(82,81,78,0.13);
  --series: #2a78d6; --accent: #eb6834; }
@media (prefers-color-scheme: dark) { .viz-root { color-scheme: dark;
  --surface: #1a1a19; --text: #f0efec; --text-2: #c3c2b7; --muted: #a3a29a;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
  --wash: rgba(195,194,183,0.14); --series: #3987e5; --accent: #d95926; } }
body { margin: 0; }
.viz-root { background: var(--surface); color: var(--text); min-height: 100vh;
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 24px; }
main { max-width: 1120px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 6px; }
h2 { font-size: 16px; margin: 28px 0 10px; }
h3 { font-size: 15px; margin: 0 0 2px; }
a { color: inherit; }
.lead, .meta { color: var(--text-2); margin: 0 0 6px; }
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0; }
.tile { border: 1px solid var(--ring); border-radius: 8px; padding: 10px 16px; min-width: 120px; }
.value { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile-label { color: var(--text-2); }
.filters { display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0; align-items: center; }
select, input { font: inherit; color: var(--text); background: var(--surface);
  border: 1px solid var(--axis); border-radius: 6px; padding: 5px 8px; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th { text-align: left; color: var(--text-2); font-weight: 600; cursor: pointer;
  border-bottom: 1px solid var(--axis); padding: 6px 8px; white-space: nowrap; }
td { border-bottom: 1px solid var(--grid); padding: 6px 8px; vertical-align: top; }
td:nth-child(4), td:nth-child(5) { white-space: nowrap; }
.num { text-align: right; }
.card { border: 1px solid var(--ring); border-radius: 8px; padding: 14px; margin: 0 0 14px; max-width: 780px; }
.badge { border: 1px solid var(--axis); border-radius: 10px; padding: 0 7px; white-space: nowrap; }
.chart { display: block; width: 100%; height: auto; margin-top: 8px; touch-action: pan-y; }
.chart text { font: 11px system-ui, sans-serif; }
.chart .title { fill: var(--text-2); font-weight: 600; font-size: 12px; }
.chart .tick, .chart .note { fill: var(--muted); }
.chart .label { fill: var(--text); paint-order: stroke; stroke: var(--surface); stroke-width: 3px; }
.grid { stroke: var(--grid); stroke-width: 1; }
.axis { stroke: var(--axis); stroke-width: 1; }
.window { fill: var(--wash); }
.line { fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.dot { fill: var(--series); stroke: var(--surface); stroke-width: 2; }
.trigger { fill: var(--accent); stroke: var(--surface); stroke-width: 2; }
.cross { stroke: var(--axis); stroke-width: 1; }
.focus { fill: var(--text); stroke: var(--surface); stroke-width: 2; pointer-events: none; }
.chart:focus-visible { outline: 2px solid var(--series); outline-offset: 2px; }
#tip { position: fixed; pointer-events: none; display: none; z-index: 10; background: var(--surface);
  border: 1px solid var(--axis); border-radius: 6px; padding: 5px 9px; }
#tip strong { display: block; }
#tip span { color: var(--text-2); }
"""

SCRIPT = """
const tip = document.getElementById('tip');
const tipValue = tip.querySelector('strong');
const tipDate = tip.querySelector('span');

function showPoint(svg, index, left, top) {
  const points = JSON.parse(svg.dataset.points);
  if (!points.length) return;
  const p = points[index];
  svg.dataset.index = index;
  const cross = svg.querySelector('.cross');
  const focus = svg.querySelector('.focus');
  cross.setAttribute('x1', p[0]); cross.setAttribute('x2', p[0]);
  cross.setAttribute('visibility', 'visible');
  focus.setAttribute('cx', p[0]); focus.setAttribute('cy', p[1]);
  focus.setAttribute('visibility', 'visible');
  tipValue.textContent = p[3];
  tipDate.textContent = p[2];
  tip.style.display = 'block';
  const box = svg.getBoundingClientRect();
  const scale = box.width / svg.viewBox.baseVal.width;
  const x = left === undefined ? box.left + p[0] * scale : left;
  const y = top === undefined ? box.top + p[1] * scale : top;
  const maxLeft = window.innerWidth - tip.offsetWidth - 8;
  tip.style.left = Math.max(8, Math.min(x + 12, maxLeft)) + 'px';
  tip.style.top = Math.max(8, y - tip.offsetHeight - 10) + 'px';
}

function hidePoint(svg) {
  svg.querySelector('.cross').setAttribute('visibility', 'hidden');
  svg.querySelector('.focus').setAttribute('visibility', 'hidden');
  tip.style.display = 'none';
}

document.querySelectorAll('svg.chart').forEach(svg => {
  const points = JSON.parse(svg.dataset.points);
  svg.addEventListener('pointermove', e => {
    if (!points.length) return;
    const box = svg.getBoundingClientRect();
    const x = (e.clientX - box.left) * svg.viewBox.baseVal.width / box.width;
    let best = 0;
    points.forEach((p, i) => {
      if (Math.abs(p[0] - x) < Math.abs(points[best][0] - x)) best = i;
    });
    showPoint(svg, best, e.clientX, e.clientY);
  });
  svg.addEventListener('pointerleave', () => hidePoint(svg));
  svg.addEventListener('blur', () => hidePoint(svg));
  svg.addEventListener('focus', () => showPoint(svg, points.length - 1));
  svg.addEventListener('keydown', e => {
    const step = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0;
    if (!step || !points.length) return;
    e.preventDefault();
    const next = Math.max(0, Math.min(points.length - 1, Number(svg.dataset.index || 0) + step));
    showPoint(svg, next);
  });
});

const quality = document.getElementById('quality');
const search = document.getElementById('search');
const shown = document.getElementById('shown');
function applyFilters() {
  const wanted = quality.value;
  const text = search.value.trim().toLowerCase().replace(/^@/, '');
  let count = 0;
  document.querySelectorAll('[data-quality]').forEach(el => {
    const match = (wanted === 'all' || el.dataset.quality === wanted)
      && el.dataset.name.includes(text);
    el.hidden = !match;
    if (match && el.tagName === 'TR') count++;
  });
  shown.textContent = count + ' shown';
}
quality.addEventListener('change', applyFilters);
search.addEventListener('input', applyFilters);

document.querySelectorAll('th').forEach((th, column) => {
  th.addEventListener('click', () => {
    const body = th.closest('table').tBodies[0];
    const ascending = th.dataset.asc !== 'true';
    th.dataset.asc = ascending;
    const value = row => row.cells[column].dataset.sort;
    const rows = [...body.rows].sort((a, b) => {
      const x = value(a), y = value(b);
      const bothNumbers = x !== '' && y !== '' && !isNaN(x) && !isNaN(y);
      const order = bothNumbers ? x - y : x.localeCompare(y);
      return ascending ? order : -order;
    });
    rows.forEach(row => body.appendChild(row));
  });
});
applyFilters();
"""


def build(conn, min_followers=0, now=None):
    """Return the report as one HTML string."""
    now = now or datetime.now(timezone.utc)
    artists = [_load(conn, row) for row in db.report_artists(conn, min_followers)]
    artists.sort(key=lambda a: (QUALITY_ORDER[a["quality"]], -(a["followers"] or 0)))
    counts = {q: sum(a["quality"] == q for a in artists) for q in QUALITY_LABELS}
    tiles = (
        _tile("Artists", len(artists))
        + "".join(_tile(QUALITY_LABELS[q], counts[q]) for q in QUALITY_LABELS)
    )
    rows = "".join(_table_row(a) for a in artists)
    cards = "".join(_card(a, now) for a in artists)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>When artists blew up</title><style>{STYLE}</style></head>
<body><div class="viz-root"><main>
<h1>When artists blew up</h1>
<p class="lead">Generated {now.strftime("%Y-%m-%d")}. A date counts as reliable only if
the data reaches back before the jump. Unsure dates may miss the real take-off because
the data starts too late or the follower window spans over a year. Origin (grew on X or
moved from elsewhere) is not yet checked.</p>
<div class="tiles">{tiles}</div>
<div class="filters">
<label>Show <select id="quality"><option value="all">All artists</option>
<option value="reliable">Reliable date</option><option value="flagged">Unsure date</option>
<option value="none">No signal</option></select></label>
<label>Search <input id="search" type="search" placeholder="username"></label>
<span id="shown" class="meta"></span></div>
<h2>Summary</h2>
<div class="scroll"><table><thead><tr><th>Artist</th><th>Followers</th><th>Joined</th>
<th>Quality</th><th>Date</th><th>Follower window</th><th>Likes jump</th><th>Trigger</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<h2>Artists</h2>
{cards}
</main></div>
<div id="tip" role="status"><strong></strong><span></span></div>
<script>{SCRIPT}</script></body></html>
"""
