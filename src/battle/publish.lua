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
-- (recorder removed 2026-07-29: the replay feature is retired -- Theo)

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
	local bm = rawget(_G, "aai_bm");
	local battle = bm.battle;

	-- phase hint: a mid-battle (re)bring-up must not regress to "loading" --
	-- the phase events fired long before us (custom-battle reload; the
	-- kernel keeps _G.aai_phase current there; nil in campaign = no change)
	local hint = rawget(_G, "aai_phase");
	if type(hint) == "string" then
		phase = hint;
	end;

	-- bisection lever (2026-07-27 loading-hang): skip ALL driver arming --
	-- no engine timers, no bm:callback, no phase-event subscriptions
	local off = io.open("data/aai_pub_off.txt", "r");
	if off then
		off:close();
		core.log("publish: DRIVERS OFF (data/aai_pub_off.txt) -- feed inactive");
		return;
	end;

	-- escalation levers for the 2026-07-27 shim-driver crash investigation
	-- (flags are read per battle load, so tests flip without a game restart):
	--   aai_feed_min.txt   -> heartbeat frames only, NO engine reads
	--   aai_feed_wait.txt  -> pump holds until the conflict phase
	--   aai_geom_on.txt    -> re-enable the geometry write (bm:buildings() is
	--                         a full-map prop enumeration -- the heaviest read
	--                         we have, and it ran on EVERY early pump while
	--                         the geometry file was pending; prime suspect for
	--                         the crash-after-a-few-pumps. Off until cleared.)
	local function lever(name)
		local f = io.open("data/" .. name, "r");
		if f then
			f:close();
			return true;
		end;
		return false;
	end;
	local FEED_MIN = lever("aai_feed_min.txt");
	local FEED_WAIT = lever("aai_feed_wait.txt");
	local GEOM_ON = lever("aai_geom_on.txt");

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
		if FEED_WAIT and phase ~= "conflict" then
			return;
		end;
		ticks = ticks + 1;
		rawset(_G, "aai_pub_ticks", ticks);
		if ticks % 40 == 0 then
			core.log("publish: alive tick " .. ticks .. " phase=" .. tostring(phase) ..
				" vt=" .. tostring(rawget(_G, "aai_tick_count")));
		end;
		local ok, st;
		if FEED_MIN then
			ok, st = true, { kind = "battle", phase = phase,
				t = ticks * TICK_MS / 1000, minimal = true };
		else
			ok, st = pcall(api.read_state, { phase = phase, t = ticks * TICK_MS / 1000 });
		end;
		if ok and st then
			json.write(PATH, st);
			if GEOM_ON then
				pcall(write_geometry, core);	-- once, to its own file
			end;
		end;
		if not ok and errs_logged < 4 then
			errs_logged = errs_logged + 1;
			core.log("publish ERROR (tick " .. ticks .. "): " .. tostring(st));
		end;
		-- piggyback hooks: battle/probe + battle/harness ride this pump
		-- (battle/reload rides it too, but LAST -- it may tear this module
		-- down and replace it, so everything else gets its tick first)
		local pt = rawget(_G, "aai_probe_tick");
		if pt then
			pt();
		end;
		local ht = rawget(_G, "aai_harness_tick");
		if ht then
			ht();
		end;
		local rt = rawget(_G, "aai_reload_tick");
		if rt then
			rt();
		end;
	end;
	----------------------------------------------------------------
	--	Driver (v3, 2026-07-27): NO engine-timer registrations of our own.
	--	ROOT CAUSE of the "dead dispatch" saga: registering our own script
	--	timers KILLS the engine's whole timer dispatch shortly after load
	--	(vanilla's own 100ms tick froze at 5 in every battle where we armed
	--	timers), and on defender-side battles it HANGS the loading screen
	--	outright. With zero registrations from us, vanilla's tick runs
	--	perfectly (2100+ ticks at exact 100ms cadence, measured in the same
	--	battle that hung).
	--
	--	So the pump rides VANILLA's dispatch slot: battle_entry's
	--	tick_increment_counter shim calls aai_pub_kick every vanilla tick
	--	(true engine-timer context, so engine reads work); pump_core's rate
	--	limiter turns 100ms kicks into ~TICK_MS pumps. Phase events flip
	--	flags only. The old registration path survives ONLY behind the
	--	opt-in flag data/aai_pub_timers.txt for forensics -- never create
	--	it in normal play.
	----------------------------------------------------------------
	local last_run = -1;
	local src_seen = {};
	local function pump_core(src)
		src_seen[src] = (src_seen[src] or 0) + 1;
		local now = os.clock();
		-- 0.9 floor (not 0.8): t is computed as ticks * TICK_MS, so admitting
		-- pumps much faster than TICK_MS inflates the feed clock
		if last_run >= 0 and (now - last_run) < (TICK_MS / 1000) * 0.9 then
			return;
		end;
		last_run = now;
		pump();
		if not completed and (ticks == 1 or ticks % 40 == 0) then
			local parts = {};
			for k, n in pairs(src_seen) do
				parts[#parts + 1] = k .. "=" .. n;
			end;
			core.log("publish: drivers " .. table.concat(parts, " "));
		end;
	end;

	_G[PUMP_NAME] = core.guarded("publish pump", function() pump_core("rep"); end);

	-- THE driver: battle_entry's vanilla-tick shim kicks this every 100ms
	-- (also open to any other context that proves it can do engine reads)
	rawset(_G, "aai_pub_kick", core.guarded("publish kick", pump_core));

	-- phase tracking: event handlers may ONLY flip the flag
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

	-- forensic opt-in ONLY: the registration that kills the dispatch
	local timers = io.open("data/aai_pub_timers.txt", "r");
	if timers then
		timers:close();
		pcall(function() battle:register_repeating_timer(PUMP_NAME, TICK_MS); end);
		core.log("publish: FORENSIC engine timer registered (aai_pub_timers.txt) " ..
			"-- expect the dispatch to die / defender battles to hang");
	end;

	core.log("publish: driver = vanilla-tick shim kick, tick " .. TICK_MS ..
		"ms -> " .. PATH .. " [min=" .. tostring(FEED_MIN) ..
		" wait=" .. tostring(FEED_WAIT) .. " geom=" .. tostring(GEOM_ON) .. "]");
end;

return M;
