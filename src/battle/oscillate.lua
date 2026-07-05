-------------------------------------------------------------------------
--	Battle unit-control test: oscillates every enemy unit along its own
--	facing — 10m forward, then back — every PERIOD_MS. First proof that
--	the AI can command units.
--
--	THE CONTEXT LAW (measured 2026-07-04): engine interface calls work
--	at script load and inside bm:callback timer callbacks, but return
--	garbage inside events.* callbacks. So event handlers here only flip
--	flags; every engine call happens in the timer loop. Squad capture
--	(units + controllers) was done at load by battle_entry.lua.
-------------------------------------------------------------------------

local M = {};

local PERIOD_MS = 5000;
local DISTANCE = 10;

local api = nil;
local squads = nil;
local plan = {};

-- read fresh positions/facings and fix each unit's oscillation axis
local function prepare(core)
	plan = {};
	for i = 1, #squads do
		local s = squads[i];
		local ok, x, z, brg = pcall(api.unit_pos, s.unit);
		if ok and type(x) == "number" and type(brg) == "number" then
			local bearing = math.rad(brg);
			plan[#plan + 1] = {
				uc = s.uc,
				ox = x,
				oz = z,
				fx = math.sin(bearing),	-- unit-forward in ground plane
				fz = math.cos(bearing),
				out = true,
			};
		end;
	end;
	core.log("oscillate: prepared " .. #plan .. "/" .. #squads .. " units");
end;

-- order every unit to its next waypoint (forward point <-> origin)
local function step(core)
	local sent, failed = 0, 0;
	for i = 1, #plan do
		local s = plan[i];
		local x, z;
		if s.out then
			x = s.ox + s.fx * DISTANCE;
			z = s.oz + s.fz * DISTANCE;
		else
			x = s.ox;
			z = s.oz;
		end;
		s.out = not s.out;
		local ok, err = pcall(api.move, s.uc, x, z, false);
		if ok then
			sent = sent + 1;
		else
			failed = failed + 1;
			if failed == 1 then
				core.log("oscillate move error: " .. tostring(err));
			end;
		end;
	end;
	core.log("oscillate: orders sent=" .. sent .. " failed=" .. failed);
end;

function M.init(core)
	local bm = rawget(_G, "aai_bm");
	api = rawget(_G, "aai_api");
	squads = rawget(_G, "aai_enemy_squads");
	if not bm or not api or type(squads) ~= "table" or #squads == 0 then
		core.log("oscillate: no captured enemy squads in this instance");
		return;
	end;

	local conflict = false;
	local finished = false;
	local prepared = false;

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

	local function cycle()
		if finished then
			core.log("oscillate: battle complete — stopped");
			return;
		end;
		if conflict then
			if not prepared then
				prepared = true;
				local ok, err = pcall(prepare, core);
				if not ok then
					core.log("oscillate: prepare FAILED: " .. tostring(err));
				end;
			end;
			if #plan > 0 then
				local ok, err = pcall(step, core);
				if not ok then
					core.log("oscillate step ERROR: " .. tostring(err));
				end;
			end;
		end;
		bm:callback(core.guarded("oscillate cycle", cycle), PERIOD_MS, "aai_oscillate");
	end;

	core.log("oscillate: timer loop armed (" .. (PERIOD_MS / 1000) ..
		"s), waiting for conflict phase");
	cycle();	-- first call runs at load (safe context); reschedules itself
end;

return M;
