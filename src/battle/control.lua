-------------------------------------------------------------------------
--	WRITE driver: AI suppression + external command loop. Polls the seq-gated
--	order mailbox data/aai_orders.txt (written by the cockpit) and imposes
--	orders on the enemy army, dispatching every action through api.issue().
--
--	Control laws (2026-07-05 investigation, reference/vanilla) -- PRESERVED:
--	 * take_control() is not durable; re-take every unit every tick.
--	 * a bare take does not cancel the in-flight order; always follow with a
--	   real order (move/halt/attack).
--	 * ordered_position() is the engine's command truth; the watchdog re-imposes
--	   position orders on drift. Attack/withdraw targets move, so those fire once
--	   and refresh periodically (no drift watchdog).
--	 * routing/shattered units cannot be ordered; skip and re-grip on rally.
--
--	Orders file grammar (unchanged -- cockpit compatibility). One command per
--	line under a monotonic seq; movement/attack share one slot per unit;
--	stances + one-shots are independent; identical replays are ignored.
--	  move <key> x z [run] | form <key> x z bearing w [run] | apos <key> x z [run]
--	  aunit <key> x z | stop <key> | flee <key> [run] | fire <key> 0|1
--	  mlee <key> 0|1 | spd <key> mult | act <key> id <rout|fearless|default|kill|taunt>
--	  abil <key> seq idx | release <key>
--	The full ACTION set (incl. shot_type/occupy_zone/teleport/set_formation/naval)
--	lives in api.ACTIONS; wiring more verbs here is an additive follow-up.
-------------------------------------------------------------------------
local M = {};

local apimod = require "battle/api";
local native = require "battle/native";
local recorder = require "battle/recorder";

local ORDERS_PATH = "data/aai_orders.txt";
local LINK_PATH = "data/aai_link.txt";
local TICK_MS = 500;
local SYNC_EVERY = 4;
local DRIFT_M = 10;
local HALT_SETTLE_M = 30;
local ADOPT_M = 2;
local OVERRIDE_LOG_CAP = 12;
local ATTACK_REFRESH = 8;	-- ticks between re-issuing a chase/withdraw order

local bridge = nil;		-- _G.aai_api (control-policy reads: flags/pos/ordered)
local squads = nil;

local last_seq = 0;
local standing = {};		-- key -> {kind, x, z, bearing, width, run}
local stance = {};		-- key -> {fire=bool, melee=bool, speed=num}
local acts = {};		-- key -> last applied one-shot id
local released = {};		-- key -> true: user gave it back; leave it alone
local ctl = {};			-- key -> per-unit control state, see touch()
local warned_unknown = {};
local sync_fail_logged = false;
local link_time = 0;		-- battle seconds, stamped on recorded commands

local function touch(key)
	local c = ctl[key];
	if not c then
		c = {
			claimed = false,
			fail_logged = false,
			blind_logged = false,
			flags_logged = false,
			broken = false,
			expected = nil,		-- {x, z} last commanded position order
			drift_seen = nil,
			overrides = 0,
			attack_issued = false,	-- aunit/flee: has the order been fired
			attack_tick = 0,
			fire_applied = nil,	-- last stance value pushed to the engine
			melee_applied = nil,
			speed_applied = nil,
			pending_act = nil,	-- one-shot queued for next tick
			pending_abil = nil,	-- one-shot ability id queued
			abil_seq = 0,		-- last ability seq (dedup)
		};
		ctl[key] = c;
	end;
	return c;
end;

local function dist2(ax, az, bx, bz)
	local dx, dz = ax - bx, az - bz;
	return dx * dx + dz * dz;
end;

local function nearnum(a, b)
	if a == nil or b == nil then
		return a == b;
	end;
	local d = a - b;
	if d < 0 then d = -d; end;
	return d < 0.05;
end;

-- a movement/attack order replaces the single standing slot; identical replays
-- are ignored so we never reset pathing
local function set_move(core, key, kind, o, counts)
	local cur = standing[key];
	local same = cur and cur.kind == kind
		and nearnum(cur.x, o.x) and nearnum(cur.z, o.z)
		and cur.run == o.run
		and nearnum(cur.bearing, o.bearing) and nearnum(cur.width, o.width);
	if same then
		counts.skip = counts.skip + 1;
		return;
	end;
	o.kind = kind;
	standing[key] = o;
	released[key] = nil;
	local c = touch(key);
	c.expected = nil;
	c.attack_issued = false;
	counts.reg = counts.reg + 1;
	core.log("control: order " .. kind .. " " .. key);
end;

local function set_stance(core, key, field, val, counts)
	local st = stance[key];
	if not st then
		st = {};
		stance[key] = st;
	end;
	if st[field] == val then
		counts.skip = counts.skip + 1;
		return;
	end;
	st[field] = val;
	local c = touch(key);
	if field == "fire" then
		c.fire_applied = nil;
	elseif field == "melee" then
		c.melee_applied = nil;
	elseif field == "speed" then
		c.speed_applied = nil;
	end;
	counts.reg = counts.reg + 1;
	core.log("control: stance " .. field .. "=" .. tostring(val) .. " " .. key);
end;

local function process_order_line(core, line, counts)
	local before = counts.reg;
	local t = {};
	for w in string.gmatch(line, "%S+") do
		t[#t + 1] = w;
	end;
	local verb, key = t[1], t[2];
	if not verb or not key then
		return;
	end;

	if verb == "move" then
		local x, z = tonumber(t[3]), tonumber(t[4]);
		if x and z then
			set_move(core, key, "move", { x = x, z = z, run = (t[5] == "run") }, counts);
		end;
	elseif verb == "form" then
		local x, z = tonumber(t[3]), tonumber(t[4]);
		local b, wd = tonumber(t[5]), tonumber(t[6]);
		if x and z and b and wd then
			set_move(core, key, "form",
				{ x = x, z = z, bearing = b, width = wd, run = (t[7] == "run") }, counts);
		end;
	elseif verb == "apos" then
		local x, z = tonumber(t[3]), tonumber(t[4]);
		if x and z then
			set_move(core, key, "apos", { x = x, z = z, run = (t[5] == "run") }, counts);
		end;
	elseif verb == "aunit" then
		local x, z = tonumber(t[3]), tonumber(t[4]);
		if x and z then
			set_move(core, key, "aunit", { x = x, z = z }, counts);
		end;
	elseif verb == "flee" then
		local cur = standing[key];
		local run = (t[3] == "run");
		if not (cur and cur.kind == "flee" and cur.run == run) then
			standing[key] = { kind = "flee", run = run };
			released[key] = nil;
			local c = touch(key);
			c.expected = nil;
			c.attack_issued = false;
			counts.reg = counts.reg + 1;
			core.log("control: order flee " .. key);
		else
			counts.skip = counts.skip + 1;
		end;
	elseif verb == "stop" then
		if standing[key] ~= nil then
			standing[key] = nil;
			local c = touch(key);
			c.expected = nil;
			c.attack_issued = false;
			counts.reg = counts.reg + 1;
			core.log("control: order stop " .. key);
		else
			counts.skip = counts.skip + 1;
		end;
		released[key] = nil;
	elseif verb == "fire" then
		set_stance(core, key, "fire", (t[3] == "1"), counts);
	elseif verb == "mlee" then
		set_stance(core, key, "melee", (t[3] == "1"), counts);
	elseif verb == "spd" then
		local m = tonumber(t[3]);
		if m then
			set_stance(core, key, "speed", m, counts);
		end;
	elseif verb == "act" then
		local id, a = tonumber(t[3]), t[4];
		if id and a then
			if (acts[key] or 0) < id then
				acts[key] = id;
				touch(key).pending_act = a;
				counts.reg = counts.reg + 1;
				core.log("control: action " .. a .. " " .. key);
			else
				counts.skip = counts.skip + 1;
			end;
		end;
	elseif verb == "abil" then
		local seq = tonumber(t[3]);
		local idx = tonumber(t[4]) or 1;	-- which ability (1 or 2)
		if seq then
			local c = touch(key);
			if (c.abil_seq or 0) < seq then
				c.abil_seq = seq;
				c.pending_abil = idx;
				counts.reg = counts.reg + 1;
				core.log("control: ability idx " .. idx .. " " .. key);
			else
				counts.skip = counts.skip + 1;
			end;
		end;
	elseif verb == "release" then
		standing[key] = nil;
		stance[key] = nil;
		released[key] = true;
		ctl[key] = nil;
		if squads and squads[key] then
			apimod.issue(squads[key], "release");
		end;
		core.log("control: released " .. key .. " back to the AI");
	end;

	if counts.reg > before then
		pcall(recorder.log_command,
			{ k = "c", t = link_time, verb = verb, key = key, line = line });
	end;
end;

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
	local prev = last_seq;

	local counts = { reg = 0, skip = 0, err = 0 };
	for line in string.gmatch(text, "[^\r\n]+") do
		local ok, err = pcall(process_order_line, core, line, counts);
		if not ok then
			counts.err = counts.err + 1;
			core.log("control: read_orders LINE ERROR [" .. tostring(line) ..
				"]: " .. tostring(err));
		end;
	end;
	last_seq = seq;

	if counts.reg > 0 or counts.err > 0 then
		core.log(string.format(
			"control: read_orders seq=%s prev=%s registered=%d skipped=%d errors=%d",
			tostring(seq), tostring(prev), counts.reg, counts.skip, counts.err));
	end;
end;

-- push our standing movement/attack order and record the expected ordered
-- position (for the drift watchdog on position orders)
local function impose(key, s, c)
	c.drift_seen = nil;
	local o = standing[key];
	if not o then
		if apimod.issue(s, "halt") then
			local ok, px, pz = pcall(bridge.unit_pos, s.unit);
			if ok and px then
				c.expected = { x = px, z = pz };
			end;
		end;
		return;
	end;
	if o.kind == "move" then
		if apimod.issue(s, "move", o) then
			c.expected = { x = o.x, z = o.z };
		end;
	elseif o.kind == "form" then
		if apimod.issue(s, "form", o) then
			c.expected = { x = o.x, z = o.z };
		end;
	elseif o.kind == "apos" then
		if apimod.issue(s, "apos", o) then
			c.expected = { x = o.x, z = o.z };
		end;
	elseif o.kind == "aunit" then
		apimod.issue(s, "aunit", o);
	elseif o.kind == "flee" then
		apimod.issue(s, "flee", o);
	end;
end;

local function apply_stances(core, key, s, c)
	local st = stance[key];
	if not st then
		return;
	end;
	if st.fire ~= nil and st.fire ~= c.fire_applied then
		if apimod.issue(s, "fire", { on = st.fire }) then
			c.fire_applied = st.fire;
		end;
	end;
	if st.melee ~= nil and st.melee ~= c.melee_applied then
		if apimod.issue(s, "melee", { on = st.melee }) then
			c.melee_applied = st.melee;
		end;
	end;
	if st.speed ~= nil and st.speed ~= c.speed_applied then
		if apimod.issue(s, "speed", { mult = st.speed }) then
			c.speed_applied = st.speed;
		end;
	end;
end;

local function apply_action(core, key, s, c)
	if c.pending_abil then
		local id = c.pending_abil;
		c.pending_abil = nil;
		apimod.issue(s, "ability", { idx = id });
		core.log("control: ability " .. tostring(id) .. " -> " .. key);
	end;
	local a = c.pending_act;
	if not a then
		return;
	end;
	c.pending_act = nil;
	if a == "kill" then
		apimod.issue(s, "kill");
	elseif a == "taunt" then
		apimod.issue(s, "taunt");
	else
		apimod.issue(s, "morale", { mode = a });
	end;
	core.log("control: applied action " .. a .. " to " .. key);
end;

-- one unit, one tick: re-take, apply stances/actions, keep it on OUR order,
-- spot AI overrides on position orders. returns 1 on first claim.
local function control_unit(core, key, s, conflict, ticks)
	local c = touch(key);

	local fl_ok, routing, shattered = pcall(bridge.unit_flags, s.unit);
	if not fl_ok and not c.flags_logged then
		c.flags_logged = true;
		core.log("control: " .. key .. " flags unreadable (" .. tostring(routing) ..
			") -- claim-only until readable");
	end;

	if fl_ok and (routing or shattered) then
		if not c.broken then
			c.broken = true;
			core.log("control: " .. key .. " broke -- waiting for rally");
		end;
		return 0;
	end;
	if c.broken then
		c.broken = false;
		c.expected = nil;		-- rally hands the unit to the AI; re-grip
		c.attack_issued = false;
		c.fire_applied = nil;		-- and re-assert stances
		c.melee_applied = nil;
		c.speed_applied = nil;
		core.log("control: " .. key .. " rallied -- re-gripped");
	end;

	local took = apimod.issue(s, "take");
	local newly = 0;
	if took and not c.claimed then
		c.claimed = true;
		c.fail_logged = false;
		newly = 1;
	elseif not took and not c.fail_logged then
		c.fail_logged = true;
		core.log("control: claim FAILED " .. key .. " (will retry every tick)");
	end;

	if not conflict or s.pre or not fl_ok then
		return newly;
	end;

	apply_stances(core, key, s, c);
	apply_action(core, key, s, c);

	local o = standing[key];

	-- attack-a-unit / withdraw: target moves, so fire once and refresh
	-- periodically; no position watchdog
	if o and (o.kind == "aunit" or o.kind == "flee") then
		if not c.attack_issued or (ticks - c.attack_tick) >= ATTACK_REFRESH then
			impose(key, s, c);
			c.attack_issued = true;
			c.attack_tick = ticks;
		end;
		return newly;
	end;

	-- position orders (move/form/apos) and halt-hold: drift watchdog
	if not c.expected then
		impose(key, s, c);
		return newly;
	end;

	local o_ok, ox, oz = pcall(bridge.unit_ordered, s.unit);
	if not (o_ok and ox) then
		if not c.blind_logged then
			c.blind_logged = true;
			core.log("control: " .. key ..
				" ordered_position unreadable -- watchdog blind for it");
		end;
		return newly;
	end;

	if dist2(ox, oz, c.expected.x, c.expected.z) <= DRIFT_M * DRIFT_M then
		c.drift_seen = nil;
		return newly;
	end;

	if not o then
		-- halt-held unit settling: follow the engine's point silently
		if dist2(ox, oz, c.expected.x, c.expected.z) <= HALT_SETTLE_M * HALT_SETTLE_M then
			c.expected = { x = ox, z = oz };
			return newly;
		end;
	else
		-- goto/attack-ground clamp or tug-of-war: adopt after two identical
		-- drifted readings instead of stuttering
		if c.drift_seen and
				dist2(ox, oz, c.drift_seen.x, c.drift_seen.z) <= ADOPT_M * ADOPT_M then
			c.expected = { x = ox, z = oz };
			c.drift_seen = nil;
			core.log(string.format(
				"control: ADOPTED %s engine destination %.1f,%.1f (unreachable/contested) -- re-drag to retry",
				key, ox, oz));
			return newly;
		end;
		c.drift_seen = { x = ox, z = oz };
	end;

	c.overrides = c.overrides + 1;
	if c.overrides <= OVERRIDE_LOG_CAP then
		core.log(string.format(
			"control: OVERRIDE %s ordered drifted to %.1f,%.1f (expected %.1f,%.1f) -- re-imposing",
			key, ox, oz, c.expected.x, c.expected.z));
		if c.overrides == OVERRIDE_LOG_CAP then
			core.log("control: " .. key .. " override log capped");
		end;
	end;
	local saved_drift = c.drift_seen;
	impose(key, s, c);
	c.drift_seen = saved_drift;
	return newly;
end;

local FLAG_PATH = "data/aai_battle_ai_on.txt";

-- Is external battle command switched on? DEFAULT OFF: with no flag file the
-- module stays fully inert and the enemy battle AI runs normally. Toggle with
-- ONE file, no rebuild/restart. Checked once per battle load.
local function control_enabled()
	local f = io.open(FLAG_PATH, "r");
	if f then f:close(); return true; end;
	return false;
end;

-- one-shot: confirm the native read path end-to-end (men via DLL == Lua men).
local function native_sanity(core)
	if not native.ok or not squads then
		return;
	end;
	for pk, ps in pairs(squads) do
		local lua_men = nil;
		pcall(function() lua_men = ps.unit:number_of_men_alive(); end);
		local nat_men = native.read(ps.unit, native.FIELD.men);
		core.log("control: NATIVE sanity " .. pk .. " men lua=" .. tostring(lua_men) ..
			" native=" .. tostring(nat_men) ..
			((lua_men and nat_men and lua_men == nat_men) and " MATCH" or " (compare)"));
		local fat = native.read(ps.unit, native.FIELD.fatigue);
		core.log("control: NATIVE fatigue " .. pk .. " = " .. tostring(fat) ..
			" (needs-confirm offset 0x1cb4)");
		break;
	end;
end;

function M.init(core)
	bridge = rawget(_G, "aai_api");
	local bm = rawget(_G, "aai_bm");
	if not apimod.ready() or not bm or not bridge or not bridge.sync_squads then
		core.log("control: api/bridge not ready (bootstrap instance) -- inactive");
		return;
	end;

	if not control_enabled() then
		core.log("control: OFF (no " .. FLAG_PATH .. ") -- enemy battle AI runs "
			.. "normally. Create that file to enable external unit control.");
		return;
	end;
	core.log("control: ON (" .. FLAG_PATH .. " present) -- external unit control active");

	pcall(os.remove, ORDERS_PATH);

	local ok0, cache, added = pcall(bridge.sync_squads);
	if ok0 then
		squads = cache;
		core.log("control: squad cache primed (" .. tostring(added) .. " units)");
	else
		core.log("control: initial sync FAILED: " .. tostring(cache));
	end;

	native_sanity(core);

	local f = io.open(LINK_PATH, "w");
	if f then
		f:write("battle_start\n");
		f:close();
	end;

	local conflict = false;
	local finished = false;
	local ticks = 0;
	local announced = false;

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
			core.log("control: battle complete -- link closed");
			return;
		end;
		ticks = ticks + 1;
		link_time = ticks * TICK_MS / 1000;

		if ticks == 1 or ticks % SYNC_EVERY == 0 then
			local ok, cache, added = pcall(bridge.sync_squads);
			if ok then
				squads = cache;
				sync_fail_logged = false;
				if added and added > 0 and ticks > 1 then
					core.log("control: squad cache +" .. added .. " new units");
				end;
			elseif not sync_fail_logged then
				sync_fail_logged = true;
				core.log("control: resync FAILED: " .. tostring(cache));
			end;
		end;

		pcall(read_orders, core);

		if conflict and not announced then
			announced = true;
			core.log("control: conflict phase -- imposing script orders on all units");
		end;

		local newly = 0;
		for key, s in pairs(squads or {}) do
			if not released[key] then
				local ok, n = pcall(control_unit, core, key, s, conflict, ticks);
				if ok and n then
					newly = newly + n;
				end;
			end;
		end;
		if newly > 0 then
			core.log("control: claimed " .. newly .. " enemy units (" ..
				(conflict and "conflict" or "deployment") .. ")");
		end;

		for key in pairs(standing) do
			if not (squads and squads[key]) then
				if not warned_unknown[key] then
					warned_unknown[key] = true;
					core.log("control: order for unknown unit " .. key ..
						" -- held until it appears");
				end;
			elseif warned_unknown[key] then
				warned_unknown[key] = nil;
				core.log("control: held order for " .. key .. " now active");
			end;
		end;

		bm:callback(core.guarded("control tick", tick), TICK_MS, "aai_control");
	end;

	core.log("control: per-tick retake + full command set via api.issue, every " ..
		TICK_MS .. "ms");
	tick();
end;

return M;
