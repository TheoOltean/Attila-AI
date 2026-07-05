-------------------------------------------------------------------------
--	AI link: applies external move orders to enemy units and KEEPS them
--	applied. Units are addressed as "alliance:army:index" keys into the
--	bridge's live squad cache (see battle_entry.lua sync_squads), which
--	is re-synced every few ticks so reinforcement units join it.
--
--	Every standing order is re-imposed (take_control + goto) each tick:
--	if the engine AI or a rally steals a unit back, we retake it within
--	one tick. Drop a unit from control with an explicit release order.
--
--	Orders file (data/aai_orders.txt), written by the viz server:
--	  seq <ms>
--	  move <a:m:i> <x> <z> [run]
--	  release <a:m:i>
-------------------------------------------------------------------------

local M = {};

local ORDERS_PATH = "data/aai_orders.txt";
local LINK_PATH = "data/aai_link.txt";
local TICK_MS = 500;
local SYNC_EVERY = 4;	-- ticks between squad-cache syncs

local api = nil;
local squads = nil;
local last_seq = 0;
local standing = {};	-- key -> {x, z, run}
local taken = {};	-- key -> true once claimed from the AI
local released = {};	-- key -> true = user gave it back; never reclaim

local function read_orders(core)
	local f = io.open(ORDERS_PATH, "r");
	if not f then
		return;
	end;
	local text = f:read("*a") or "";
	f:close();

	local seq = tonumber(string.match(text, "^seq (%d+)"));
	if not seq or seq <= last_seq then
		return;
	end;
	last_seq = seq;

	for line in string.gmatch(text, "[^\r\n]+") do
		local key, x, z, run =
			string.match(line, "^move (%d+:%d+:%d+) ([-%d%.]+) ([-%d%.]+) ?(%a*)");
		if key then
			standing[key] = { x = tonumber(x), z = tonumber(z), run = (run == "run") };
			core.log("ai_link: standing order " .. key .. " -> " .. x .. ", " .. z);
		else
			local rkey = string.match(line, "^release (%d+:%d+:%d+)");
			if rkey then
				standing[rkey] = nil;
				taken[rkey] = nil;
				released[rkey] = true;
				if squads and squads[rkey] then
					pcall(api.release, squads[rkey].uc);
				end;
				core.log("ai_link: released " .. rkey .. " back to the AI");
			end;
		end;
	end;
end;

local function suppress_all(core)
	local claimed = 0;
	for key, s in pairs(squads or {}) do
		if not taken[key] and not released[key] then
			if pcall(api.take, s.uc) then
				taken[key] = true;
				claimed = claimed + 1;
			end;
		end;
	end;
	if claimed > 0 then
		core.log("ai_link: claimed " .. claimed ..
			" enemy units from the AI (full suppression)");
	end;
end;

local function enforce(core)
	local applied, failed = 0, 0;
	for key, order in pairs(standing) do
		local s = squads and squads[key];
		if s then
			pcall(api.take, s.uc);
			local ok = pcall(api.move, s.uc, order.x, order.z, order.run);
			if ok then applied = applied + 1; else failed = failed + 1; end;
		end;
	end;
	if failed > 0 then
		core.log("ai_link: enforce failed on " .. failed .. " units");
	end;
end;

function M.init(core)
	local bm = rawget(_G, "aai_bm");
	api = rawget(_G, "aai_api");
	if not bm or not api or not api.sync_squads then
		core.log("ai_link: no bridge (bootstrap instance) — inactive");
		return;
	end;

	pcall(os.remove, ORDERS_PATH);
	local ok0, cache, added = pcall(api.sync_squads);
	if ok0 then
		squads = cache;
		core.log("ai_link: squad cache primed (" .. tostring(added) .. " units)");
	else
		core.log("ai_link: initial sync FAILED: " .. tostring(cache));
	end;
	local f = io.open(LINK_PATH, "w");
	if f then
		f:write("battle_start\n");
		f:close();
	end;

	local conflict = false;
	local finished = false;
	local ticks = 0;

	-- flags only — no engine calls allowed in event context
	local ev = _G.events;
	if type(ev) == "table" then
		if ev.BattleConflictPhaseCommenced then
			ev.BattleConflictPhaseCommenced[#ev.BattleConflictPhaseCommenced + 1] =
				function() conflict = true; end;
		end;
		if ev.BattleCompleted then
			ev.BattleCompleted[#ev.BattleCompleted + 1] =
				function() finished = true; end;
		end;
	else
		conflict = true;
	end;

	local function tick()
		if finished then
			local g = io.open(LINK_PATH, "w");
			if g then
				g:write("battle_over\n");
				g:close();
			end;
			core.log("ai_link: battle complete — link closed");
			return;
		end;
		ticks = ticks + 1;
		if conflict then
			if ticks == 1 or ticks % SYNC_EVERY == 0 then
				local ok, cache, added = pcall(api.sync_squads);
				if ok then
					squads = cache;
					if added and added > 0 then
						core.log("ai_link: squad cache +" .. added ..
							" new units (reinforcements)");
					end;
				end;
				pcall(suppress_all, core);
			end;
			pcall(read_orders, core);
			pcall(enforce, core);
		end;
		bm:callback(core.guarded("ai_link tick", tick), TICK_MS, "aai_ai_link");
	end;

	core.log("ai_link: FULL AI SUPPRESSION mode, enforcing every " .. TICK_MS .. "ms");
	tick();
end;

return M;
