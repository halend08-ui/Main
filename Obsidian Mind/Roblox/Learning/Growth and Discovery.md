---
tags: [roblox, growth, marketing]
---
# Growth and discovery

Back to [[Roblox Home]]

## How Roblox decides what to recommend (the short version)
Roblox recommends games that people **click**, **play for a long time** and **come back to**. So:
1. **Icon + thumbnail + title** decide whether people click.
2. **The first 60 seconds** decide whether they stay.
3. **Retention (D1/D7)** and **session length** decide whether Roblox keeps recommending you.

## Icon and thumbnails
- Bright, simple, readable at a tiny size. One focal point: a character mid-jump, big text.
- Thumbnails: show the *most exciting* moment (the beanstalk climb, a spinner near-miss).
- Run thumbnail A/B tests in Creator Hub and keep the winner.
- Title formula: `[Emoji] Theme Obby [hook]`, e.g. "🌱 Grow an Obby! [50 LEVELS]". Update tags like `[UPDATE 8]` sell freshness.

## The first 60 seconds
- Play within 5 seconds: no long intro.
- Level 1 should be almost impossible to fail. It should feel like progress, not a test.
- Show the goal (progress bar, "Level 1/50", the next zone name).
- Something fun in the first minute (a bounce pad, a trail preview).

## Getting players
- **Shorts/TikTok/Reels**: clips of fails, "only 1% beat level 50", satisfying runs. Put the link in bio.
- **Friends and group**: a group with a free reward for joining.
- **Ads**: small daily budget (Creator Hub ads) *after* retention is decent. Paying for players who leave straight away wastes money.
- **Updates**: each update is a reason to post and bumps the game's freshness.

## Analytics to watch
Creator Hub > your experience > Analytics.

| Metric | What it tells me | Rough target |
|---|---|---|
| D1 retention | Did they like it? | 10%+ is ok, 15%+ is good for an obby |
| D7 retention | Will they come back? | 3%+ ok, 5%+ good |
| Avg session length | Is it engaging? | 10+ min |
| Payer conversion | Is the shop working? | 1–3% |
| Level funnel | *Where* do players quit? | Log it yourself (see below) |

> [!tip] Log a custom event when each level is completed (`AnalyticsService:LogProgressionEvent` / funnel events). The level where most players quit is the one to make easier, or the place to offer a Skip.

(These targets are my own rough goals, not official Roblox numbers. Compare against the benchmarks Creator Hub shows for similar games.)
