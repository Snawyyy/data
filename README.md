# data

A database of visual and digital artists on X, built to study why big artists
blew up. The focus is artists who started small on X, not ones who brought an
audience over from another platform.

## Setup

Needs Python 3.10+. Data comes from the public FxTwitter API
(api.fxtwitter.com), so no X account or token is needed.

## Usage

```bash
# find artists on its own: starts from artists listed on Wikidata, then
# crawls who each artist follows and reposts. Ctrl-C any time; rerun resumes.
python -m artistdb scan --min-followers 10000

# optional: add specific usernames, one per line or first CSV column
python -m artistdb seed seeds.txt

# re-fetch every profile; each run logs a follower snapshot
python -m artistdb refresh

# pull timelines (only new tweets after the first run)
python -m artistdb tweets --min-followers 10000

# old follower counts from archived profile pages on the Wayback Machine
python -m artistdb history --min-followers 10000

# find when each artist blew up (use --user @name for one artist);
# pull full timelines first with: python -m artistdb tweets --full
python -m artistdb blowup

# mark whether an artist grew on X or moved from elsewhere
python -m artistdb origin @someartist native --notes "first post 2019, 40 followers"

python -m artistdb stats
```

Data goes to `artists.db` (change with `--db`).

## How blow-ups are found

Two signals, stored in the `blowups` table:

- **followers**: in the follower history (live snapshots plus Wayback
  archives), the stretch with the fastest growth relative to size.
- **engagement**: the first month where median likes on the artist's own posts
  reach 5x their earlier 6-month level and stay at 3x for the next 3 months.
  The most liked post in the 60 days before is saved as the likely trigger.

## Limits to know

- FxTwitter has no search, so the scan starts from about 7,600 artist handles on
  Wikidata and grows through following lists and reposts. It finds artists
  connected to others, so isolated ones can be missed.
- The scan keeps accounts whose bio looks like an artist's (see `--bio-match`).
  Some studios and fan accounts still slip through; review them.
- FxTwitter randomly fails about half its requests; the client retries, so
  runs are slow (about 1 to 3 seconds per request).
- Timelines only go back so far, so very active artists lose early history.
- Wayback archives carry follower counts mostly before 2020; later years lean
  on engagement and on running `refresh` regularly.
