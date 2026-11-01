# Analysis plan: why artists blew up, and how to reproduce it

The goal is a playbook: what artists who grew on X did before, during, and
after their blow-up, and what sets them apart from artists who did not blow up.

## 1. Who counts (native vs migrated)

Only artists who grew on X are studied. A score marks likely migrants for
manual review with `artistdb origin`:

- Followers at the earliest data point (first Wayback archive or first posts).
  A native artist starts small, under about 2k.
- Account age at blow-up. Years of small posting points to native growth.
- Bio and website links to Instagram, YouTube, TikTok, ArtStation, Twitch.
- First posts already getting high likes compared to followers (an audience
  that arrived from somewhere else).
- Big name outside X (Wikidata entry, studio job, published work) before the
  account grew.

## 2. Shape of the blow-up

Per artist:

- Date of blow-up, and years from account creation to it (the grind time).
- Followers at the start, and the multiplier (after / before).
- Speed: months the fast growth lasted.
- Single spike vs staircase: how many separate breakout events.
- Retention: likes and follower growth 6 and 12 months later, compared to the
  peak. Did it stick or fade?
- Era: blow-up year, grouped by X algorithm changes (algorithmic timeline
  2016, For You tab and view counts 2023, creator payouts 2023).

## 3. Anatomy of the trigger post

- Format: image, multiple images, video, GIF, text only.
- Subject: fan art (which franchise) vs original character or world.
  Detect from hashtags, franchise names, and bio.
- Process content: WIP, timelapse, before/after, "draw this in your style",
  art challenges (#portfolioday, #DTIYS, #artvsartist).
- Text: length, hashtags, mentions, language, call to action.
- Timing: hour and weekday, and whether it matched a release, trend, or
  event for that franchise.
- Riding a wave: was it a reply to or quote of a bigger account?
- Engagement mix: reposts / likes (shareability), quotes / likes
  (discussion), bookmarks / likes (saved as reference), views / followers
  (reach beyond the existing audience).
- Ratio to the artist's baseline (already stored as `trigger_ratio`).

## 4. Behavior before the blow-up (6 to 12 months)

- Posting rate and consistency (gaps between posts, their spread).
- Share of posts with art vs talk vs replies.
- Community activity: replies to other artists, quotes, reposts of others.
- Consistency of subject and style: one fandom or character vs many.
- Number of near-misses: posts that spiked but did not stick, and how long
  before the real blow-up they started.
- Hashtag and challenge use.

## 5. Behavior after the blow-up

- Did posting rate go up or down?
- Follow-ups on the same subject or series (cashing in) vs moving on.
- Pinned post, commissions opened, shop or Patreon links added to the bio
  (from refreshed profiles).

## 6. Network

- Which known artists follow them and repost them (the crawl graph).
- Whether bigger accounts reposted or quoted the trigger post.
- Clusters: fandom or regional circles (for example JP vs EN art
  communities) and whether blow-ups spread inside a circle.

## 7. Control group (needed to avoid survivorship bias)

Studying only winners shows what winners did, not what caused it. The scan
also keeps artists who did not blow up, with similar start size and age
(for example 500 to 10k followers after 3+ years). Every metric above is
compared between the two groups.

## 8. Statistics

- For each metric: median and spread across blown-up artists.
- Before vs after for the same artist (paired comparison).
- Blown-up vs control (Mann-Whitney test, effect size).
- Which features predict a blow-up: logistic regression or a small decision
  tree, kept simple enough to read.
- Results split by era and by community.

## 9. Data still to collect

- Media type and count per post, bookmarks, hashtags, mentions.
- Quote and reply targets, with their follower counts.
- Full timelines for control artists.
- Regular `refresh` runs for live follower snapshots going forward.

## 10. Output

- Report page: summary table, per-artist charts of followers and likes with
  the blow-up marked, and a card with the trigger post and key numbers.
- Pattern summary: the most common paths (for example "fan art video during a
  franchise release, after 2 years of steady weekly posting").
- Playbook: steps an artist can follow, each backed by the numbers above and
  how much more often it appears in blown-up artists than in the control group.
