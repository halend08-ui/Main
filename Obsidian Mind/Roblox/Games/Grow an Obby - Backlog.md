---
tags: [roblox, backlog, grow-an-obby]
updated: 2026-09-23
---
# Grow an Obby: backlog

Back to [[Grow an Obby]]

Ordered by impact. The first things that matter are **retention** (do players stay?) and **conversion** (do they buy?).

## Now: check that v8 works
- [ ] Playtest all 12 new level types in Studio: *Test > Start* with 2 or more players to catch server/client bugs
- [ ] Playtest in the live game on **phone** and on PC
- [ ] Write the level types into [[Grow an Obby#Current state (v8)]]
- [ ] Check the Developer Console (F9) in the live game for script errors
- [ ] Confirm saving works: leave partway through, rejoin, and you should be on the same level

## Next: retention
- [ ] **Daily reward / login streak:** the cheapest way to raise D1/D7 retention
- [ ] **Stage leaderboard** (a `leaderstats` folder with Stage and Wins), because players compete
- [ ] **Rebirth / prestige:** after beating level 50, restart with a permanent perk or cosmetic, which gives replay value
- [ ] **Group reward:** join the group for a free trail, which builds a following you can notify
- [ ] Hard levels should never feel unfair. Watch where players quit (see [[Growth and Discovery#Analytics to watch]])

## Next: monetization
- [ ] **Starter pack** bundle (a Pass): trail + speed at a discount
- [ ] **VIP pass**: chat tag, VIP-only shortcut, 2× win count
- [ ] **More trails/pets** as cosmetics (players buy cosmetics easily and they don't hurt fairness)
- [ ] **Premium Payouts** earn money automatically from Premium players' playtime, so retention also pays
- [ ] Test prices: raise or lower one price at a time and compare conversion after a week

## Later: content
- [ ] Zone 6+ in batches of 10 levels, each with one new mechanic (see [[Obby Design Principles]])
- [ ] Seasonal events (Halloween garden, winter garden) that reuse existing level types with a new look
- [ ] Badges for each zone completed (a cheap goal that feels good)

## Ideas
- Player "garden" plot that grows as you beat levels (fits the theme and gives a reason to come back)
- Timed speedrun mode with a global leaderboard (OrderedDataStore)
