-------------------------------------------------------------------------
--	READ driver: the out-of-process feed. Drives api.read_state() from the
--	engine's NATIVE repeating timer and writes the full battle snapshot to
--	data/aai_battle.json every 500 ms (consumed by the cockpit's /state).
--
--	GEOMETRY is written ONCE per battle to its own file (data/aai_battle_geometry.json)
--	and fetched once by the cockpit -- it is STATIC bulk. Do NOT fold it into the
--	per-tick snapshot: bm:buildings() returns every prop on the map (measured
--	16,149 on a city map -> an 890 KB snapshot re-encoded + rewritten twice a
--	second). The bridge filters to tactical structures (walls/gates/towers/
--	barricades) and caps the list.
--
--	This is the old state_json exporter's driver, unchanged in every load-bearing
--	way: register_repeating_timer (survives the siege load->deploy handover where
--	bm:callback dies), event handlers flip the phase flag only (engine calls are
--	broken in event context), nil-frame guard keeps the last good frame on screen.
--	Only the per-unit assembly moved into api.read_state.
-------------------------------------------------------------------------
local M = {};

local api = require "battle/api";
local json = require "aai_json";
local recorder = require "battle/recorder";

local PATH = "data/aai_battle.json";
local GEOM_PATH = "data/aai_battle_geometry.json";
local TICK_MS = 500;
local GEOM_CAP = 2000;		-- mirrors BLD_CAP in battle_entry.lua's api.buildings

local phase = "loading";
local ticks = 0;
local errs_logged = 0;
local geom_written = false;

-- Static bulk: written once, fetched once. Never goes in the tick snapshot.
local function write_geometry(core)
	if geom_written then
		return;
	end;
	local blds = api.buildings();		-- nil until readable; {} on prop-less maps
	if blds == nil then
		return;
	end;
	-- Retry while empty for the first ~20 ticks (10 s): the building list may not
	-- be enumerable yet at load. After that accept empty -- open-field maps
	-- genuinely have no tactical structures, and we must not re-scan every tick
	-- (the raw list is ~16k props before filtering).
	if #blds == 0 and ticks < 20 then
		return;
	end;
	geom_written = true;
	json.write(GEOM_PATH, {
		kind = "geometry",
		battle_id = rawget(_G, "aai_battle_id"),
		bounds = {},			-- {} -> [] (UI falls back to unit-bbox extent)
		buildings = blds,
		count = #blds,
		capped = (#blds >= GEOM_CAP),
	});
	core.log("publish: geometry written once -- " .. #blds ..
		" tactical buildings -> " .. GEOM_PATH);
end;

function M.init(core)
	if not api.ready() then
		core.log("publish: api not ready (bootstrap instance) -- feed inactive");
		return;
	end;
	local battle = rawget(_G, "aai_bm").battle;

	-- Drive from the engine's native repeating timer (calls the GLOBAL named
	-- PUMP_NAME every TICK_MS). It drives the battle_manager itself, so it
	-- survives the siege load->deploy handover that orphans a bm:callback chain.
	local PUMP_NAME = "aai_publish_pump";
	local completed = false;
	local function pump()
		if completed then
			return;
		end;
		if phase == "complete" then
			completed = true;
			pcall(function()
				json.write(PATH, { kind = "battle", phase = "complete",
					t = ticks * TICK_MS / 1000 });
			end);
			return;
		end;
		ticks = ticks + 1;
		if ticks % 40 == 0 then
			core.log("publish: alive tick " .. ticks .. " phase=" .. tostring(phase));
		end;
		local ok, st = pcall(api.read_state, { phase = phase, t = ticks * TICK_MS / 1000 });
		if ok and st then
			json.write(PATH, st);
			-- Record only the FIGHT. A battle left idling in deployment recorded
			-- every tick for ~6 h and produced a 59 GB .jsonl; the deployment
			-- idle has no replay value. (recorder also hard-caps at 512 MB.)
			if phase == "conflict" then
				pcall(recorder.log_state, st);
			end;
			pcall(write_geometry, core);		-- once, to its own file
		end;
		if not ok and errs_logged < 4 then
			errs_logged = errs_logged + 1;
			core.log("publish ERROR (tick " .. ticks .. "): " .. tostring(st));
		end;
	end;
	_G[PUMP_NAME] = core.guarded("publish pump", pump);

	-- Re-arm a fresh native timer from the deploy/conflict events ONLY if the
	-- load-time timer is dead (it dies at the siege load->deploy handover).
	-- Guard on ticks: registering a second live timer just doubles the write
	-- rate and inflates the battle clock `t` (the old exporter did exactly that).
	local reg_seq = 0;
	local function reregister(tag)
		if ticks > 0 then
			core.log("publish: load-time timer alive (tick " .. ticks ..
				") -- no reregister needed at " .. tag);
			return;
		end;
		reg_seq = reg_seq + 1;
		local nm = "aai_publish_pump_" .. tag .. "_" .. reg_seq;
		_G[nm] = core.guarded("publish pump", pump);
		local ok = pcall(function() battle:register_repeating_timer(nm, TICK_MS); end);
		core.log("publish: load-time timer DEAD -- reregistered '" .. nm ..
			"' from " .. tag .. " ok=" .. tostring(ok));
	end;

	-- phase tracking: event handlers may ONLY flip the flag (+ re-arm the timer)
	local ev = _G.events;
	if type(ev) == "table" then
		if ev.BattleDeploymentPhaseCommenced then
			ev.BattleDeploymentPhaseCommenced[#ev.BattleDeploymentPhaseCommenced + 1] =
				function() phase = "deployment"; reregister("deploy"); end;
		end;
		if ev.BattleConflictPhaseCommenced then
			ev.BattleConflictPhaseCommenced[#ev.BattleConflictPhaseCommenced + 1] =
				function() phase = "conflict"; reregister("conflict"); end;
		end;
		if ev.BattleCompleted then
			ev.BattleCompleted[#ev.BattleCompleted + 1] =
				function() phase = "complete"; end;
		end;
	end;

	local reg_ok = false;
	pcall(function()
		battle:register_repeating_timer(PUMP_NAME, TICK_MS); reg_ok = true;
	end);
	core.log("publish: engine timer every " .. TICK_MS .. "ms reg_ok=" ..
		tostring(reg_ok) .. " -> " .. PATH);
end;

return M;
