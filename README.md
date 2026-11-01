# data

A database of visual and digital artists on X, built to study why big artists
blew up. The focus is artists who started small on X, not ones who brought an
audience over from another platform.

## Setup

Needs Python 3.10+ and an X API bearer token (paid developer account).

```bash
export X_BEARER_TOKEN=...
```

## Usage

```bash
# import usernames, one per line or first CSV column
python -m artistdb seed seeds.txt

# find artists posting art in the last 7 days with 10k+ followers
python -m artistdb discover --min-followers 10000 --limit 1000

# re-fetch every profile; each run logs a follower snapshot
python -m artistdb refresh

# pull timelines (only new tweets after the first run)
python -m artistdb tweets --min-followers 10000

# mark whether an artist grew on X or moved from elsewhere
python -m artistdb origin @someartist native --notes "first post 2019, 40 followers"

python -m artistdb stats
```

Data goes to `artists.db` (change with `--db`).

## Limits to know

- X only returns about the newest 3200 tweets per user, so very active artists
  lose their early history.
- X gives current follower counts only. Growth over time comes from running
  `refresh` regularly and from engagement on old tweets.
- Recent search covers only the last 7 days.
