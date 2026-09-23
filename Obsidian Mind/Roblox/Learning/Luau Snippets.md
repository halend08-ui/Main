---
tags: [roblox, luau, snippets]
---
# Luau snippets

Back to [[Luau and Studio Fundamentals]]

Copy-paste patterns. Adjust names to match `ObbyConfig`.

## Kill parts by tag (one script for all of them)
```lua
-- ServerScriptService/Hazards.server.lua
local CollectionService = game:GetService("CollectionService")

local function hookKill(part: BasePart)
	part.Touched:Connect(function(hit)
		local humanoid = hit.Parent and hit.Parent:FindFirstChildOfClass("Humanoid")
		if humanoid and humanoid.Health > 0 then
			humanoid.Health = 0
		end
	end)
end

for _, part in CollectionService:GetTagged("Kill") do hookKill(part) end
CollectionService:GetInstanceAddedSignal("Kill"):Connect(hookKill)
```

## Checkpoints (server only lets players move forward one stage)
```lua
local Players = game:GetService("Players")
local checkpoints = workspace:WaitForChild("Checkpoints")

for _, cp in checkpoints:GetChildren() do
	local n = tonumber(cp.Name)
	cp.Touched:Connect(function(hit)
		local player = Players:GetPlayerFromCharacter(hit.Parent)
		if not player then return end
		local stage = player.leaderstats.Stage
		if n == stage.Value + 1 then -- no skipping ahead by touching
			stage.Value = n
		end
	end)
end
```

## Saving that doesn't lose data
```lua
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")
local store = DataStoreService:GetDataStore("PlayerData_v1")
local loaded = {} -- only save players whose data loaded successfully

local function load(player)
	local ok, data = pcall(store.GetAsync, store, "u_" .. player.UserId)
	if not ok then
		player:Kick("Couldn't load your data, please rejoin.")
		return
	end
	data = data or { Stage = 1, Wins = 0 }
	loaded[player] = data
	-- build leaderstats from data here
end

local function save(player)
	local data = loaded[player]
	if not data then return end
	data.Stage = player.leaderstats.Stage.Value
	pcall(store.UpdateAsync, store, "u_" .. player.UserId, function()
		return data
	end)
end

Players.PlayerAdded:Connect(load)
Players.PlayerRemoving:Connect(function(p) save(p); loaded[p] = nil end)
game:BindToClose(function()
	for _, p in Players:GetPlayers() do task.spawn(save, p) end
	task.wait(3)
end)
```

## ProcessReceipt (Developer Products, e.g. Skip Level)
```lua
local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")
local Config = require(game.ReplicatedStorage.ObbyConfig)

local handlers = {
	[Config.PRODUCTS.SkipLevel] = function(player)
		local stage = player.leaderstats.Stage
		stage.Value += 1
		-- teleport player to the new checkpoint
		return true
	end,
}

MarketplaceService.ProcessReceipt = function(receipt)
	local player = Players:GetPlayerByUserId(receipt.PlayerId)
	local handler = handlers[receipt.ProductId]
	if not player or not handler then
		return Enum.ProductPurchaseDecision.NotProcessedYet -- Roblox retries later
	end
	local ok, granted = pcall(handler, player)
	if ok and granted then
		return Enum.ProductPurchaseDecision.PurchaseGranted
	end
	return Enum.ProductPurchaseDecision.NotProcessedYet
end
```
> Make granting **idempotent** for expensive items: store `receipt.PurchaseId` so a retry never grants twice.

## Pass check (Speed Boost)
```lua
local function applyPasses(player, character)
	local ok, owns = pcall(MarketplaceService.UserOwnsGamePassAsync, MarketplaceService, player.UserId, Config.PASSES.SpeedBoost)
	if ok and owns then
		character:WaitForChild("Humanoid").WalkSpeed = 24
	end
end

MarketplaceService.PromptGamePassPurchaseFinished:Connect(function(player, passId, purchased)
	if purchased and passId == Config.PASSES.SpeedBoost and player.Character then
		applyPasses(player, player.Character)
	end
end)
```

## Tweened moving platform
```lua
local TweenService = game:GetService("TweenService")
local part = workspace.Course.MovingPlatform
local info = TweenInfo.new(2, Enum.EasingStyle.Sine, Enum.EasingDirection.InOut, -1, true)
TweenService:Create(part, info, { CFrame = part.CFrame * CFrame.new(0, 0, 20) }):Play()
```
