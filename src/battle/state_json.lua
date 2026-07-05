-------------------------------------------------------------------------
--	Battle state exporter: writes data/aai_battle.json with EVERY unit
--	on both sides (position, bearing, men, ammo, status) plus the
--	camera, twice a second. Consumed by ai/aai_viz.py.
--
--	Same acquisition/tick pattern as battle/telemetry.lua: interface
--	from the battle_entry bridge (_G.aai_bm), reads via the pcall +
--	function-unwrap helper, ticking on bm:callback (the context law),
--	event handlers flip the phase string only.
-------------------------------------------------------------------------

local M = {};

local json = require "aai_json";

local PATH = "data/aai_battle.json";
local TICK_MS = 500;

local bm = nil;
local battle = nil;
local phase = "loading";
local ticks = 0;

local function read(obj, method, ...)
	local args = { ... };
	local ok, value = pcall(function() return obj[method](obj, unpack(args)); end);
	if not ok then
		return nil;
	end;
	if type(value) == "function" then
		local ok2, value2 = pcall(value);
		if ok2 then
			return value2;
		end;
		return nil;
	end;
	return value;
end;

local function vec(v)
	if not v then
		return nil;
	end;
	return { x = read(v, "get_x"), y = read(v, "get_y"), z = read(v, "get_z") };
end;

local function unit_entry(unit)
	local entry = {
		name = tostring(read(unit, "name") or "?"),
		pos = vec(read(unit, "position")),
		bearing = read(unit, "bearing"),
		men = read(unit, "number_of_men_alive"),
		men0 = read(unit, "initial_number_of_men"),
		ammo = read(unit, "ammo_left"),
		routing = read(unit, "is_routing") and true or false,
		shattered = read(unit, "is_shattered") and true or false,
		cavalry = read(unit, "is_cavalry") and true or false,
		melee = read(unit, "is_in_melee") and true or false,
		moving = read(unit, "is_moving") and true or false,
		idle = read(unit, "is_idle") and true or false,
		xp = read(unit, "experience_level"),
		ordered = vec(read(unit, "ordered_position")),
		ordered_bearing = read(unit, "ordered_bearing"),
	};
	local ty = read(unit, "type");
	if ty ~= nil then
		entry.type = tostring(ty);
	end;
	local fat = read(unit, "fatigue_state");
	if fat == nil then
		fat = read(unit, "fatigue_level");
	end;
	if fat ~= nil then
		entry.fatigue = tostring(fat);
	end;
	return entry;
end;

local function snapshot()
	local state = {
		kind = "battle",
		phase = phase,
		t = ticks * TICK_MS / 1000,
		player_alliance = read(battle, "local_alliance"),
	};

	local cam = read(battle, "camera");
	if cam then
		state.camera = {
			pos = vec(read(cam, "position")),
			target = vec(read(cam, "target")),
		};
	end;

	local alliances = {};
	local alist = read(battle, "alliances");
	local acount = (alist and read(alist, "count")) or 0;
	for a = 1, acount do
		local armies_out = {};
		local alliance = read(alist, "item", a);
		local armies = alliance and read(alliance, "armies");
		local mcount = (armies and read(armies, "count")) or 0;
		for m = 1, mcount do
			local units_out = {};
			local army = read(armies, "item", m);
			local units = army and read(army, "units");
			local ucount = (units and read(units, "count")) or 0;
			for u = 1, ucount do
				local unit = read(units, "item", u);
				if unit then
					local e = unit_entry(unit);
					e.i = u;
					units_out[#units_out + 1] = e;
				end;
			end;
			armies_out[#armies_out + 1] = { units = units_out, n = ucount };
		end;
		alliances[#alliances + 1] = { armies = armies_out };
	end;
	state.alliances = alliances;

	json.write(PATH, state);
end;

function M.init(core)
	local mgr = rawget(_G, "aai_bm");
	if not (mgr and mgr.battle) then
		core.log("battle state_json: no bridge (bootstrap instance) — inactive");
		return;
	end;
	bm = mgr;
	battle = mgr.battle;

	local function tick()
		if phase == "complete" then
			return;
		end;
		ticks = ticks + 1;
		local ok, err = pcall(snapshot);
		if not ok and ticks == 1 then
			core.log("battle state_json ERROR: " .. tostring(err));
		end;
		bm:callback(core.guarded("state_json tick", tick), TICK_MS, "aai_state_json_tick");
	end;

	-- phase tracking: plain assignments only in event context
	local ev = _G.events;
	if type(ev) == "table" then
		if ev.BattleDeploymentPhaseCommenced then
			ev.BattleDeploymentPhaseCommenced[#ev.BattleDeploymentPhaseCommenced + 1] =
				function() phase = "deployment"; end;
		end;
		if ev.BattleConflictPhaseCommenced then
			ev.BattleConflictPhaseCommenced[#ev.BattleConflictPhaseCommenced + 1] =
				function() phase = "conflict"; end;
		end;
		if ev.BattleCompleted then
			ev.BattleCompleted[#ev.BattleCompleted + 1] =
				function() phase = "complete"; end;
		end;
	end;

	core.log("battle state_json: every " .. TICK_MS .. "ms -> " .. PATH);
	tick();
end;

return M;
