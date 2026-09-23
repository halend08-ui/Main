---
tags: [roblox, game, obby, active]
status: live
version: v8
file: GrowAnObby.rbxlx
created: 2026-09-22
updated: 2026-09-23
---
# 🌱 Grow an Obby

Back to [[Roblox Home]] · Next: [[Grow an Obby - Backlog]]

A garden-themed obby. Players climb through 5 zones of plants and hazards, and the difficulty ramps up slowly.

## Current state (v8)
- **v8 deployed:** 12 new level types, live in new servers. *Still to do: playtest them in Studio and in the live game.*
- **50 levels in 5 zones.** Hazards unlock as you go: mushrooms, poison swamps, thorn spinners, wilting leaves, growing sprouts, cactus fields and a beanstalk climb.
- **Saving:** progress and wins save automatically (DataStore).

> [!todo] Write down the 12 new v8 level types here
> The old session's full transcript couldn't be read from here. Next time Studio is open, list each new level type (name, what it does, which zone).

| # | Level type | Behavior | Zone | Notes |
|---|---|---|---|---|
| 1 | | | | |

## UI
- Title screen with a Play button
- Top bar with the level counter, zone name and progress bar
- **Shop**: Skip Level, Speed Boost, Super Jump and Rainbow Trail
- **Settings**: music on/off and Hide Other Players
- A pulsing Skip button in the corner (the main place purchases start)
- Zone pop-ups when you enter a new zone
- Win screen with confetti, Play Again and a Wins counter
- Scales to fit phones and big screens (most Roblox players are on mobile)

## How it's built
- **Config:** `ReplicatedStorage > ObbyConfig` holds `PRODUCTS`, `PASSES`, `IMAGES` and `MUSIC_ID`, plus the price, name and description of every shop item.
- **Parts are named by what they do.** One script finds every part with a given name and handles it:
  - `Kill`: kills on touch
  - `Spinner`: spins and kills
  - `Fader`: wilts away and comes back
  - `Grower`: grows and shrinks
  - `Bounce`: mushroom bounce pad
- **Images:** any image ID left at `0` falls back to an emoji, so the UI never breaks.
- **Free in Studio:** every purchase is free while testing in Studio.

## Monetization
| Item | Type | Price |
|---|---|---|
| Skip Level | Developer Product (can be bought again) | R$ 25 |
| Speed Boost | Pass | R$ 99 |
| Super Jump | Pass | R$ 149 |
| Rainbow Trail | Pass | R$ 49 |

The IDs go in `ObbyConfig > PRODUCTS / PASSES`. See [[Monetization Playbook]].

## Art
- Icons were made with Higgsfield: Logo ("GROW AN OBBY"), Shop (bag), Settings (gear), Skip (arrows), Speed (sneaker), Jump (spring), Trail (rainbow) and Trophy.
- Upload them with Studio > View > Asset Manager > Images > Bulk Import, then right-click each one > Copy Asset ID and paste it into `IMAGES`.

## Setup checklist
- [ ] Publish (File > Publish to Roblox)
- [ ] Game Settings > Security > **Enable Studio Access to API Services** (needed for saving)
- [ ] Create the Developer Product and Passes, then paste their IDs into ObbyConfig
- [ ] Upload the icons and paste their image IDs
- [ ] Music ID (optional)
- [ ] Icon and thumbnails at Creator Dashboard > Places > Configure
- [ ] Publish again

## Version history
| Version | What changed |
|---|---|
| v1–v7 | Built up from the [[Obby (21 Stages)]] prototype to the 50-level, 5-zone game with full UI and shop *(fill in details)* |
| v8 | 12 new level types, live in new servers |
