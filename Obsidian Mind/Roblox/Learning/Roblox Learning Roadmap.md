---
tags: [roblox, learning]
---
# Roblox learning roadmap

Back to [[Roblox Home]]

Tick things off as I learn them. Each stage ends with something **shipped**.

## Stage 1: Studio basics ✅ (mostly done with the obby)
- [x] Parts, anchoring, CanCollide, Transparency, Materials
- [x] Workspace / ReplicatedStorage / ServerScriptService / StarterGui / StarterPlayer
- [x] Publishing, Game Settings, API access
- [ ] Studio shortcuts: Ctrl+D duplicate, Alt-drag, the snap and rotate increments, the Model tab
- [ ] Organize with Folders and Models, and **group repeating obstacles into reusable models**

## Stage 2: Luau scripting
- [ ] Variables, tables, functions, loops, `if`, string formatting
- [ ] Events: `.Touched`, `Players.PlayerAdded`, `CharacterAdded`, connecting and disconnecting
- [ ] `task.wait`, `task.spawn`, `task.delay` (not the old `wait()`)
- [ ] **ModuleScripts:** how `ObbyConfig` works, and splitting code into modules
- [ ] `CollectionService` tags as a cleaner alternative to naming parts by what they do
- [ ] Type annotations (`--!strict`) to catch bugs early

➡ Read: [[Luau and Studio Fundamentals]] · [[Luau Snippets]]

## Stage 3: Client/server and data
- [ ] Script vs LocalScript vs ModuleScript, and where each one runs
- [ ] RemoteEvents / RemoteFunctions, and **validating everything on the server**
- [ ] DataStoreService: `GetAsync`, `UpdateAsync`, `pcall`, retries, `BindToClose`
- [ ] Session locking / data loss (or use the ProfileStore library)
- [ ] MarketplaceService: `ProcessReceipt` done correctly

## Stage 4: Game feel and UI
- [ ] TweenService for UI and moving platforms
- [ ] Sounds, particles and camera shake: small effects make the game feel good
- [ ] UI for mobile: `UIScale`, `UIAspectRatioConstraint`, big tap targets, safe area
- [ ] Tutorialize without text: show, don't tell

## Stage 5: Live ops (being a *good* developer, not only a builder)
- [ ] Read Creator Hub analytics every week: retention, session length, conversion
- [ ] A/B test thumbnails (Creator Hub supports thumbnail experiments)
- [ ] Update cadence: small updates weekly, bigger ones monthly
- [ ] Community: group, Discord/Talking Stadium, update logs in-game
- [ ] Ads, sponsored placement, Shorts/TikTok clips

➡ Read: [[Growth and Discovery]] · [[Monetization Playbook]]

## Stage 6: Next genre
Once Grow an Obby is steady, try a genre with a longer game loop (simulator, tycoon, "grow a ___" collector). These keep players for longer and earn more per player. Reuse the config, save and shop systems.

## Resources
- create.roblox.com/docs: the official docs and tutorials
- DevForum (devforum.roblox.com): announcements, community resources
- Creator Hub > Analytics for your own game
- Play the top obbies for 10 minutes each and write down what they do in the first 60 seconds
