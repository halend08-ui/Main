---
tags: [roblox, learning, luau]
---
# Luau and Studio fundamentals

Back to [[Roblox Learning Roadmap]] · Code: [[Luau Snippets]]

## Where scripts live and run
| Place | Runs on | Use for |
|---|---|---|
| `ServerScriptService` (Script) | Server | Game rules, saving, purchases, checkpoints |
| `ReplicatedStorage` (ModuleScript) | Both | Shared config (`ObbyConfig`), shared code, RemoteEvents |
| `ServerStorage` | Server only | Things the client must never see (map templates, admin tools) |
| `StarterPlayerScripts` (LocalScript) | Client | Input, camera, local effects |
| `StarterGui` (LocalScript) | Client | UI |

## The golden rule: never trust the client
Exploiters can change anything on their own client and can fire any RemoteEvent with any arguments.
- The **server** decides what stage a player is on. The client only displays it.
- When the client asks "skip my stage", the server checks that the player actually paid (a receipt or a Pass).
- Check every remote's arguments for type and range, and rate-limit spammy ones.
- Touch-based checkpoints: only move a player *forward* by one stage at a time (stops them teleporting ahead).

## Replication
- Changes the server makes to Workspace show up for every client.
- Changes a client makes stay on that client (useful for "Hide Other Players" and local-only effects).
- Moving hazards (Spinner, Fader, Grower) can be animated **on the client** so they look smooth and cost the server less. The **kill check** still has to be trustworthy: either the server checks too, or it's a low-stakes obby hazard that's fine to leave to the client.

## DataStores (saving)
- Only works in Studio when **Enable Studio Access to API Services** is on.
- Always wrap calls in `pcall`. They can fail and are rate-limited.
- Use `UpdateAsync` instead of `SetAsync` for anything that could be overwritten.
- Save on `PlayerRemoving` **and** in `game:BindToClose` (server shutdown).
- Keep one table per player (`{Stage=, Wins=, Owned=}`) instead of many separate keys.
- If a load fails, **don't** let the player play on default data that then overwrites their real save.

## Purchases
- **Passes:** bought once, owned forever. Check with `MarketplaceService:UserOwnsGamePassAsync`. React to purchases in-game with `PromptGamePassPurchaseFinished`.
- **Developer Products:** can be bought again (Skip Level). They **must** go through `MarketplaceService.ProcessReceipt`, which returns `PurchaseGranted` only after the item was actually granted. Roblox retries otherwise.
- There can be only **one** `ProcessReceipt` callback in the whole game, so route every product through it.

## Performance
- Anchor everything that doesn't need physics.
- Turn on `Workspace.StreamingEnabled` for big maps (important with 50+ levels on phones).
- Use one script looping over tagged parts instead of a script inside every part.
- Clean up connections when things are destroyed.
