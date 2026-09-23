---
tags: [roblox, moc]
created: 2026-09-23
---
# 🎮 Roblox (home note)

This is the hub note for everything about making Roblox games: my games, what I've learned and what to do next.

## My games
- [[Grow an Obby]]: main project. 50 levels, 5 zones, full UI and shop. **v8 is live** with 12 new level types.
- [[Obby (21 Stages)]]: first prototype. A simple 21-stage obby with Skip Stage and Speed Boost.

## Working notes
- [[Grow an Obby - Backlog]]: what to add next, in priority order
- [[Session Log]]: what got built in each Claude session

## Learning to be a good Roblox dev
1. [[Roblox Learning Roadmap]]: the path from beginner to shipping good games
2. [[Luau and Studio Fundamentals]]: client vs server, RemoteEvents, DataStores
3. [[Luau Snippets]]: code patterns I can copy
4. [[Monetization Playbook]]: Developer Products, Passes, pricing
5. [[Growth and Discovery]]: icons, thumbnails, retention and getting players
6. [[Obby Design Principles]]: level design for obbies
7. [[Launch Checklist]]: go through this before every publish

## Rules I follow
- **Never trust the client.** Purchases, saves and stage progress are all decided on the server.
- **Retention beats everything.** Roblox recommends games that keep players, so polish the first 5 minutes.
- **Keep all settings in one config module** (`ReplicatedStorage > ObbyConfig`) so changes don't mean hunting through scripts.
- **Name parts by what they do** (`Kill`, `Spinner`, `Fader`, `Grower`, `Bounce`) so one script handles every part of that type.
- **Ship small updates often** and read the analytics after each one.
