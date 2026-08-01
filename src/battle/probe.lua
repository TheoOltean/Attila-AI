-------------------------------------------------------------------------
--	PROBE HARNESS: exercises every unverified T1 capability in a live
--	battle and logs a verdict per item (grep the log for "PROBE").
--	Also writes data/aai_probe.json for the cockpit.
--
--	DEFAULT-OFF: self-gates on data/aai_probe_on.txt (create to arm,
--	delete to disarm -- no rebuild). Works in ANY campaign battle; probes
--	that need missing context (siege buildings, reinforcements, ships)
--	log SKIP and get re-run in a later battle that has them.
--
--	Design rules (SCRIPTING.md): engine calls only from the engine-timer
--	pump; event handlers flip flags only; every engine touch is pcall'd;
--	writes go to ONE guinea-pig enemy unit (take_control first, halt
--	after take). Vectors need the entry env -> made via aai_bless.
-------------------------------------------------------------------------
local M = {};

local json = require "aai_json";
local ABILITIES = require "battle/db_abilities";	-- unit type -> ability keys

local PATH = "data/aai_probe.json";
local FLAG = "data/aai_probe_on.txt";
local DIVIDER = 2;		-- host pump is 500ms; run probe logic every 2nd call
local host_calls = 0;

local corel = nil;
local bm, battle, bridge = nil, nil, nil;
local mkvec = nil;			-- blessed v(x, z)
local phase = "loading";
local ticks = 0;
local armed_cache, armed_check = nil, 0;

local results = {};			-- ordered {name=, r=} entries
local plan = {};			-- FIFO of {name=, at=, fn=}
local plan_built = false;
local plan_fails = 0;		-- failed build attempts (gives up at 60)
local plan_done = false;
local cmd_events = 0;
local cmd_queue = {};		-- PLAIN {nm=,extra=} rows extracted in-handler (never
							-- the engine userdata: it may not outlive the callback,
							-- and a later deref is a C-side crash pcall can't catch)
local cmd_captured = 0;		-- rows extracted (cap 200, mirrors cmd_logged)
local cmd_logged = 0;
local cmd_armed = false;	-- flag-file cache refreshed in-handler every 25 events
local attrib_armed = false;	-- data/aai_attrib_on.txt (THROWAWAY battles only):
			-- the gated get_unit retest, ISOLATED this time (the feed-poll
			-- suspect is reverted) -- the outcome is decisive either way
local cmd_last_vt = nil;	-- vanilla tick count at the previous telemetry check
local cmd_vt_clock = -1;	-- os.clock when the vanilla count last advanced
local cmd_vt_alive = true;	-- dispatch aliveness at the previous check (flip logs)

--------------------------------------------------------------------------
--	small utils
--------------------------------------------------------------------------
local function log(text)
	corel.log("PROBE " .. tostring(text));
end;

-- pcall + function-unwrap (same trick as battle/api.lua)
local function read(obj, method, ...)
	local args = { ... };
	local ok, value = pcall(function() return obj[method](obj, unpack(args)); end);
	if not ok then
		return nil, tostring(value);
	end;
	if type(value) == "function" then
		local ok2, value2 = pcall(value);
		if ok2 then
			return value2;
		end;
		return nil, "fn-unwrap failed";
	end;
	return value;
end;

local function show(v)
	if v == nil then
		return "nil";
	end;
	return type(v) .. ":" .. tostring(v);
end;

local function record(name, verdict)
	results[#results + 1] = { name = name, r = tostring(verdict) };
	log(name .. " -> " .. tostring(verdict));
end;

-- call obj:method(...) purely for the verdict string
local function try_call(name, obj, method, ...)
	local args = { ... };
	local ok, err = pcall(function() obj[method](obj, unpack(args)); end);
	record(name, ok and "OK(no error)" or ("ERR " .. tostring(err)));
	return ok;
end;

-- enumerate an engine object's real metatable (debug.getmetatable pierces
-- the __metatable decoy) -- the way reference/reflect was collected
local function dump_methods(name, obj)
	if obj == nil then
		record(name .. ".methods", "SKIP object nil");
		return;
	end;
	local keys = {};
	pcall(function()
		local mt = debug.getmetatable(obj);
		local function collect(t)
			if type(t) ~= "table" then
				return;
			end;
			for k, _ in pairs(t) do
				keys[#keys + 1] = tostring(k);
			end;
		end;
		if type(mt) == "table" then
			collect(mt);
			collect(rawget(mt, "__index"));
		end;
	end);
	table.sort(keys);
	if #keys == 0 then
		record(name .. ".methods", "none enumerable (mt hidden or fn __index)");
	else
		record(name .. ".methods", "[" .. table.concat(keys, ",") .. "]");
	end;
end;

--------------------------------------------------------------------------
--	plan machinery: linear step queue, one step max per tick
--------------------------------------------------------------------------
local plan_at = 0;
local function queue(gap_ticks, name, fn)
	plan_at = plan_at + gap_ticks;
	plan[#plan + 1] = { name = name, at = plan_at, fn = fn };
end;

--------------------------------------------------------------------------
--	context helpers (run inside the pump only)
--------------------------------------------------------------------------
local function enemy_alliance_ix()
	local pa = read(battle, "local_alliance");
	if type(pa) ~= "number" then
		pa = 1;
	end;
	return (pa == 1) and 2 or 1, pa;
end;

local function first_player_unit()
	local _, pa = enemy_alliance_ix();
	local armies = read(battle, "alliances");
	local al = armies and read(armies, "item", pa);
	local ar = al and read(al, "armies");
	local a1 = ar and read(ar, "item", 1);
	local us = a1 and read(a1, "units");
	if us and type(read(us, "count")) == "number" and read(us, "count") >= 1 then
		return read(us, "item", 1);
	end;
	return nil;
end;

-- guinea pigs out of the squad cache: g_abil (has DB abilities), g_ranged,
-- g_any; plus the enemy army/alliance objects
local G = {};
local function pick_guineas()
	local cache = bridge.sync_squads();
	local keys = {};
	for k, _ in pairs(cache) do
		keys[#keys + 1] = k;
	end;
	table.sort(keys);
	for _, k in ipairs(keys) do
		local e = cache[k];
		if not e.pre then
			local ty = read(e.unit, "type");
			local rng = read(e.unit, "missile_range");
			if not G.any then
				G.any = e; G.any_key = k;
			end;
			if not G.abil and ty and ABILITIES[tostring(ty)] then
				G.abil = e; G.abil_key = k; G.abil_type = tostring(ty);
			end;
			if not G.ranged and type(rng) == "number" and rng > 0 then
				G.ranged = e; G.ranged_key = k;
			end;
		end;
	end;
	local eix = enemy_alliance_ix();
	local als = read(battle, "alliances");
	G.alliance = als and read(als, "item", eix);
	local ars = G.alliance and read(G.alliance, "armies");
	G.army = ars and read(ars, "item", 1);
end;

local function grab(entry)
	-- ownership law: take is not durable + does not cancel in-flight orders
	pcall(function() bridge.take(entry.uc); end);
	pcall(function() bridge.halt(entry.uc); end);
end;

--------------------------------------------------------------------------
--	READ probes (safe in CONFLICT only -- see the phase gate in pump())
--------------------------------------------------------------------------
local function build_read_plan()
	queue(0, "reads.unit", function()
		local e = G.any;
		if not e then
			record("reads.unit", "SKIP no units");
			return;
		end;
		local u = e.unit;
		local names = { "is_naval", "width", "ordered_width", "is_leaving_battle",
			"is_currently_garrisoned", "is_hidden", "is_valid_target",
			"number_of_enemies_killed", "unary_of_men_alive" };
		for _, nm in ipairs(names) do
			local val, err = read(u, nm);
			record("unit:" .. nm, err and ("ERR " .. err) or show(val));
		end;
		local p = read(u, "position_of_officer");
		record("unit:position_of_officer", p and
			(show(read(p, "get_x")) .. "," .. show(read(p, "get_z"))) or "nil");
		local pu = first_player_unit();
		if pu then
			record("unit:unit_distance(player1)", show(read(u, "unit_distance", pu)));
			record("unit:unit_in_range(player1)", show(read(u, "unit_in_range", pu)));
		else
			record("unit:unit_distance", "SKIP no player unit");
		end;
	end);

	queue(1, "reads.army", function()
		if not G.army then
			record("reads.army", "SKIP no army");
			return;
		end;
		record("army:army_handicap", show(read(G.army, "army_handicap")));
		record("army:is_commander_invincible", show(read(G.army, "is_commander_invincible")));
		local rs = read(G.army, "get_reinforcement_ships");
		record("army:get_reinforcement_ships:count", rs and show(read(rs, "count")) or "nil");
		local sh = read(G.army, "ships");
		record("army:ships:count", sh and show(read(sh, "count")) or "nil");
		if sh and type(read(sh, "count")) == "number" and read(sh, "count") >= 1 then
			dump_methods("ship", read(sh, "item", 1));
		end;
	end);

	queue(1, "reads.weather", function()
		local w = read(bm, "weather");
		record("bm:weather()", show(w));
		dump_methods("weather", w);
		if w then
			for _, nm in ipairs({ "rain", "raining", "is_raining", "wind",
				"wind_direction", "wind_strength", "temperature", "fog", "snow" }) do
				local val, err = read(w, nm);
				if not err then
					record("weather:" .. nm, show(val));
				end;
			end;
		end;
	end);

	queue(1, "reads.assault_equipment", function()
		local ae = read(battle, "assault_equipment");
		record("battle:assault_equipment()", show(ae));
		dump_methods("assault_equipment", ae);
		if ae then
			-- real method names per the 2026-07-28 dump: vehicle_count/vehicle_item
			record("assault_equipment:vehicle_count", show(read(ae, "vehicle_count")));
			record("assault_equipment:vehicle_item(1)", show(read(ae, "vehicle_item", 1)));
		end;
	end);

	queue(1, "reads.buildings", function()
		-- QUARANTINED: building-list access from the shim-driver context
		-- crashed the game twice on 2026-07-27/28 -- once in write_geometry's
		-- bm:buildings() enumeration, once HERE right after buildings:count
		-- returned 3 (so it is the access itself, not volume). Re-enable
		-- deliberately with data/aai_bld_on.txt in a throwaway battle.
		local f = io.open("data/aai_bld_on.txt", "r");
		if not f then
			record("reads.buildings",
				"QUARANTINED (buildings access crashes from shim ctx; aai_bld_on.txt to test)");
			return;
		end;
		f:close();
		local blds = read(battle, "buildings");
		local n = blds and read(blds, "count");
		record("battle:buildings:count", show(n));
		if not (blds and type(n) == "number" and n > 0) then
			record("reads.buildings", "SKIP no buildings (field map)");
			return;
		end;
		-- first few TACTICAL pieces via the bridge filter (walls/gates/towers)
		local tact = bridge.buildings();
		record("tactical buildings", show(#tact));
		local b = read(blds, "item", 1);
		for _, nm in ipairs({ "is_on_fire", "is_destroyed", "is_intact",
			"is_garrisoned", "currently_garrisoned", "capacity",
			"alliance_owner_id", "health", "name" }) do
			local val, err = read(b, nm);
			record("building:" .. nm, err and ("ERR " .. err) or show(val));
		end;
	end);
end;

--------------------------------------------------------------------------
--	WRITE probes: conflict phase only, one guinea pig
--------------------------------------------------------------------------
local BEHAVIOURS = { "shield_wall", "phalanx", "schiltrom", "guard_mode", "skirmish" };
local GROUP_FORMS = { "ordered_line", "line", "column", "wedge", "block", "circle" };

local function build_write_plan()
	local e = G.abil or G.any;
	if not e then
		queue(1, "writes", function() record("writes", "SKIP no controllable unit"); end);
		return;
	end;
	local key = G.abil_key or G.any_key;
	queue(1, "writes.intro", function()
		record("guinea", key .. " type=" .. show(read(e.unit, "type")) ..
			" abil_type=" .. tostring(G.abil_type));
		grab(e);
	end);

	-- behaviour/formation toggles: on -> verify -> off -> verify
	for _, beh in ipairs(BEHAVIOURS) do
		queue(2, "beh." .. beh .. ".on", function()
			grab(e);
			try_call("uc:change_behaviour_active(" .. beh .. ",true)",
				e.uc, "change_behaviour_active", beh, true);
		end);
		queue(3, "beh." .. beh .. ".verify", function()
			record("verify is_behaviour_active(" .. beh .. ")",
				show(read(e.unit, "is_behaviour_active", beh)));
			pcall(function() e.uc:change_behaviour_active(beh, false); end);
		end);
	end;

	-- ability activation: the unit's REAL ability ids from the DB roster
	local abils = (G.abil_type and ABILITIES[G.abil_type]) or {};
	for i = 1, math.min(#abils, 4) do
		local id = abils[i];
		queue(2, "ability." .. id, function()
			grab(e);
			try_call("uc:perform_special_ability(\"" .. id .. "\")",
				e.uc, "perform_special_ability", id);
		end);
		queue(3, "ability." .. id .. ".verify", function()
			record("verify current_special_ability",
				show(read(e.unit, "current_special_ability")));
			record("verify is_behaviour_active(special_ability)",
				show(read(e.unit, "is_behaviour_active", "special_ability")));
		end);
	end;
	queue(2, "ability.by_index", function()
		grab(e);
		try_call("uc:perform_special_ability(1)", e.uc, "perform_special_ability", 1);
	end);

	-- group formations (blind candidates; error text may name the valid set)
	for _, gf in ipairs(GROUP_FORMS) do
		queue(1, "group_formation." .. gf, function()
			grab(e);
			try_call("uc:change_group_formation(" .. gf .. ")",
				e.uc, "change_group_formation", gf);
		end);
	end;

	-- frontage nudges: read ordered_width around inc/dec
	queue(2, "width.inc", function()
		grab(e);
		record("pre ordered_width", show(read(e.unit, "ordered_width")));
		try_call("uc:increment_formation_width x3", e.uc, "increment_formation_width");
		pcall(function() e.uc:increment_formation_width(); end);
		pcall(function() e.uc:increment_formation_width(); end);
	end);
	queue(4, "width.verify", function()
		record("post ordered_width", show(read(e.unit, "ordered_width")));
		pcall(function() e.uc:decrement_formation_width(); end);
	end);

	-- misc single-shots
	queue(1, "move_speed", function()
		grab(e);
		try_call("uc:change_move_speed(2)", e.uc, "change_move_speed", 2);
	end);
	queue(1, "attack_line", function()
		grab(e);
		local p = read(e.unit, "position");
		if p and mkvec then
			local x, z = read(p, "get_x"), read(p, "get_z");
			try_call("uc:attack_line(v,v,run)", e.uc, "attack_line",
				mkvec(x + 30, z), mkvec(x + 30, z + 60), true);
		else
			record("uc:attack_line", "SKIP no pos/vec");
		end;
	end);
	queue(1, "visibility+ui", function()
		try_call("uc:set_always_visible_to_all(true)", e.uc, "set_always_visible_to_all", true);
		try_call("uc:start_celebrating()", e.uc, "start_celebrating");
		try_call("uc:highlight(true)", e.uc, "highlight", true);
		try_call("uc:hide_unit_card(true)", e.uc, "hide_unit_card", true);
	end);
	queue(2, "visibility+ui.undo", function()
		pcall(function() e.uc:set_always_visible_to_all(false); end);
		pcall(function() e.uc:highlight(false); end);
		pcall(function() e.uc:hide_unit_card(false); end);
		try_call("uc:change_enabled(true)", e.uc, "change_enabled", true);
	end);

	-- unit-object writes: men + ammo, with before/after reads
	queue(1, "kill_number_of_men", function()
		record("pre men", show(read(e.unit, "number_of_men_alive")));
		try_call("unit:kill_number_of_men(5)", e.unit, "kill_number_of_men", 5);
	end);
	queue(2, "kill_number_of_men.verify", function()
		record("post men", show(read(e.unit, "number_of_men_alive")));
	end);
	if G.ranged then
		local r = G.ranged;
		queue(1, "set_ammo", function()
			record("pre ammo", show(read(r.unit, "ammo_left")));
			try_call("unit:set_current_ammo_unary(0.5)", r.unit, "set_current_ammo_unary", 0.5);
		end);
		queue(2, "set_ammo.verify", function()
			record("post ammo", show(read(r.unit, "ammo_left")));
		end);
	end;

	-- scratch controller: group binding verbs
	queue(1, "controller.bind", function()
		if not G.army then
			record("controller.bind", "SKIP no army");
			return;
		end;
		local ok, uc2 = pcall(function() return G.army:create_unit_controller(); end);
		if not ok or not uc2 then
			record("create_unit_controller", "ERR " .. tostring(uc2));
			return;
		end;
		try_call("uc2:add_all_units()", uc2, "add_all_units");
		try_call("uc2:clear_all()", uc2, "clear_all");
		try_call("uc2:add_group()", uc2, "add_group");
	end);

	-- reinforcements: force one onto the field (vanilla-style)
	queue(1, "deploy_reinforcement", function()
		if not G.army then
			record("deploy_reinforcement", "SKIP no army");
			return;
		end;
		local rus = read(G.army, "get_reinforcement_units");
		local n = rus and read(rus, "count");
		if not (rus and type(n) == "number" and n > 0) then
			record("deploy_reinforcement", "SKIP no reinforcements this battle");
			return;
		end;
		local ru = read(rus, "item", 1);
		try_call("reinf_unit:deploy_reinforcement(true)", ru, "deploy_reinforcement", true);
	end);

	-- siege set: only when tactical buildings exist. Same quarantine as
	-- reads.buildings -- every path in here touches building objects.
	queue(1, "siege.setup", function()
		local f = io.open("data/aai_bld_on.txt", "r");
		if not f then
			record("siege.*",
				"QUARANTINED (buildings access crashes from shim ctx; aai_bld_on.txt to test)");
			return;
		end;
		f:close();
		local tact = bridge.buildings();
		if #tact == 0 then
			record("siege.*", "SKIP no tactical buildings this battle");
			return;
		end;
		-- nearest tactical piece to the guinea (engine object via get_building_near)
		local p = read(e.unit, "position");
		local x, z = read(p, "get_x"), read(p, "get_z");
		local b = nil;
		pcall(function() b = bm:get_building_near(x, z); end);
		record("bm:get_building_near", show(b) .. " name=" .. (b and show(read(b, "name")) or "-"));
		G.building = b;
	end);
	queue(1, "siege.climb", function()
		if not G.building then return; end;
		grab(e);
		try_call("uc:climb_building(b)", e.uc, "climb_building", G.building);
	end);
	queue(15, "siege.climb.verify", function()
		if not G.building then return; end;
		record("verify is_currently_garrisoned", show(read(e.unit, "is_currently_garrisoned")));
	end);
	queue(1, "siege.leave", function()
		if not G.building then return; end;
		try_call("uc:leave_building()", e.uc, "leave_building");
	end);
	queue(5, "siege.defend", function()
		if not G.building then return; end;
		grab(e);
		try_call("uc:defend_building(b)", e.uc, "defend_building", G.building);
	end);
	queue(3, "siege.attack_building", function()
		if not G.building then return; end;
		try_call("uc:attack_building(b)", e.uc, "attack_building", G.building);
	end);
	queue(3, "siege.fire", function()
		if not G.building then return; end;
		try_call("building:change_on_fire(true)", G.building, "change_on_fire", true);
	end);
	queue(2, "siege.fire.verify", function()
		if not G.building then return; end;
		record("verify building:is_on_fire", show(read(G.building, "is_on_fire")));
		pcall(function() G.building:change_on_fire(false); end);
	end);
	queue(1, "siege.deployables", function()
		try_call("uc:interact_with_deployable()", e.uc, "interact_with_deployable");
		try_call("uc:select_deployable_object()", e.uc, "select_deployable_object");
		try_call("uc:occupy_vehicle()", e.uc, "occupy_vehicle");
	end);

	-- alliance/planner layer (guinea must be RELEASED for planner orders)
	queue(1, "planner", function()
		if not G.alliance then
			record("planner", "SKIP no alliance obj");
			return;
		end;
		try_call("alliance:force_ai_plan_type_attack()", G.alliance, "force_ai_plan_type_attack");
		local ok, pl = pcall(function() return G.alliance:create_ai_unit_planner(); end);
		record("alliance:create_ai_unit_planner", ok and show(pl) or ("ERR " .. tostring(pl)));
		if ok and pl then
			dump_methods("ai_unit_planner", pl);
			pcall(function() bridge.release(e.uc); end);
			try_call("planner:add_units(guinea)", pl, "add_units", e.unit);
			local p = read(e.unit, "position");
			if p and mkvec then
				try_call("planner:move_to_position(v)", pl, "move_to_position",
					mkvec(read(p, "get_x") + 50, read(p, "get_z")));
			end;
		end;
	end);
	queue(5, "planner.end", function()
		grab(e);	-- re-take after the planner experiment
	end);

	-- battle-level knobs
	queue(1, "battle.speed", function()
		try_call("battle:modify_battle_speed(2)", battle, "modify_battle_speed", 2);
	end);
	queue(2, "battle.speed.restore", function()
		try_call("battle:restore_battle_speed()", battle, "restore_battle_speed");
	end);
	queue(1, "battle.clock", function()
		try_call("battle:change_victory_countdown_limit(-1)", battle,
			"change_victory_countdown_limit", -1);
		try_call("battle:change_conflict_time_update_overridden(true)", battle,
			"change_conflict_time_update_overridden", true);
	end);
	queue(2, "battle.clock.restore", function()
		try_call("battle:change_conflict_time_update_overridden(false)", battle,
			"change_conflict_time_update_overridden", false);
	end);
	queue(1, "battle.blind", function()
		-- signature-unknown: called argless purely to harvest the error text
		try_call("battle:trigger_projectile_launch()", battle, "trigger_projectile_launch");
		try_call("battle:place_naval_mine()", battle, "place_naval_mine");
	end);

	queue(2, "done", function()
		pcall(function() bridge.release(e.uc); end);
		record("PROBE PLAN COMPLETE", "cmd_events_seen=" .. cmd_events);
		plan_done = true;
	end);
end;

--------------------------------------------------------------------------
--	pump
--------------------------------------------------------------------------
local function armed()
	if ticks - armed_check >= 5 or armed_cache == nil then
		armed_check = ticks;
		local f = io.open(FLAG, "r");
		armed_cache = (f ~= nil);
		if f then
			f:close();
		end;
	end;
	return armed_cache;
end;

local function write_results()
	pcall(function()
		json.write(PATH, {
			kind = "probe",
			battle_id = rawget(_G, "aai_battle_id"),
			t = ticks,
			phase = phase,
			done = plan_done,
			cmd_events = cmd_events,
			results = results,
		});
	end);
end;

local function pump()
	host_calls = host_calls + 1;
	if host_calls % DIVIDER ~= 0 then
		return;
	end;
	ticks = ticks + 1;
	if ticks == 1 or ticks % 60 == 0 then
		log("pump alive tick " .. ticks .. " armed=" .. tostring(armed()) ..
			" phase=" .. tostring(phase));
	end;
	if not armed() then
		return;
	end;
	-- drain command rows (already extracted to plain strings in-handler --
	-- the engine userdata must never be dereferenced after its callback)
	while #cmd_queue > 0 and cmd_logged < 200 do
		local c = table.remove(cmd_queue, 1);
		cmd_logged = cmd_logged + 1;
		log("cmd[" .. cmd_logged .. "/" .. cmd_events .. "] name=" .. tostring(c.nm) ..
			(c.extra ~= "" and (" " .. c.extra) or ""));
	end;
	-- CONFLICT-GATED end to end (2026-07-28): arming at deployment froze the
	-- vanilla dispatch at the very pump that ran step 1 -- no step verdict, no
	-- STEP ERROR, nothing pcall could catch. publish's read_state walked every
	-- unit in deployment on that same pump and was fine, so plain reads are
	-- deployment-safe; probe STEP execution is not. The armed-at-conflict run
	-- (2026-07-28 15:30) executed 22 steps cleanly. Detail: SCRIPTING.md.
	if phase ~= "conflict" then
		return;
	end;
	if not plan_built then
		-- latch plan_built only on SUCCESS: pick_guineas walks live engine
		-- objects and throws when the bridge reads come back dead -- latching
		-- first would leave the probe silently inert for the whole battle
		if plan_fails == 0 then
			log("ARMED -- building plan (phase=" .. phase .. ")");
		end;
		local ok, err = pcall(function()
			pick_guineas();
			plan_at = ticks;
			build_read_plan();
			build_write_plan();
		end);
		if ok then
			plan_built = true;
			log("plan: " .. #plan .. " steps queued");
		else
			plan = {};		-- drop any partially-queued steps; retry next tick
			plan_fails = plan_fails + 1;
			if plan_fails == 1 or plan_fails % 20 == 0 then
				log("plan build FAILED (try " .. plan_fails .. "): " .. tostring(err));
			end;
			if plan_fails >= 60 then
				plan_built = true;
				log("plan build GIVING UP after 60 tries");
			end;
		end;
	end;
	local step = plan[1];
	if step and ticks >= step.at then
		table.remove(plan, 1);
		-- breadcrumb BEFORE execution: if a step dies uncatchably (the
		-- deployment freeze left no trace), the log names the killer
		log("step: " .. step.name);
		local ok, err = pcall(step.fn);
		if not ok then
			record(step.name, "STEP ERROR " .. tostring(err));
		end;
		write_results();
	end;
end;

--------------------------------------------------------------------------
function M.init(core)
	corel = core;
	bm = rawget(_G, "aai_bm");
	battle = bm and bm.battle or nil;
	bridge = rawget(_G, "aai_api");
	if not (bm and battle and bridge) then
		core.log("probe: no bridge (bootstrap instance) -- inactive");
		return;
	end;
	local bless = rawget(_G, "aai_bless");
	if bless then
		mkvec = bless(function(x, z) return v(x, z); end);
	end;

	-- order capture + plain-Lua telemetry. The engine calls this global for
	-- command events (fires thousands of times in live combat).
	--
	-- CONTEXT LAW, HARD FORM (2026-07-28 17:30 battle): NO engine calls in
	-- here -- not even pcall'd reads. The old telemetry read
	-- battle:local_alliance() every 25th event; in every battle where that
	-- read FAILED (nil) it was harmless, but the first battle where it
	-- SUCCEEDED (attacker side, local_alliance=1), the vanilla timer
	-- dispatch froze at that exact second -- probe idle, no steps run.
	-- Detail: SCRIPTING.md. Telemetry below is plain Lua only: dispatch
	-- aliveness inferred from _G.aai_tick_count + os.clock. Event-userdata
	-- getters (the event's OWN fields, in the capture block) are the one
	-- exception: 6000+ events under a live dispatch without harm.
	-- the captured rows are shared via _G.aai_cmd_rows: the probe pump drains
	-- them in probe sessions, the HARNESS pump drains them into data/aai_cmds.json
	-- for the cockpit's order-type panel in harness sessions
	rawset(_G, "aai_cmd_rows", cmd_queue);
	rawset(_G, "aai_probe_cmd", core.guarded("probe cmd handler", function(event)
		cmd_events = cmd_events + 1;
		if cmd_events == 1 or cmd_events % 25 == 0 then
			local f = io.open(FLAG, "r");
			cmd_armed = (f ~= nil);
			if f then
				f:close();
			end;
			if not cmd_armed then
				-- harness sessions capture too (probe flag off, harness flag on)
				local fh = io.open("data/aai_harness_on.txt", "r");
				if fh then
					fh:close();
					cmd_armed = true;
				end;
			end;
			local fa = io.open("data/aai_attrib_on.txt", "r");
			attrib_armed = (fa ~= nil);
			if fa then
				fa:close();
			end;
			if cmd_armed then
				local vt = tonumber(rawget(_G, "aai_tick_count")) or -1;
				local now = os.clock();
				if cmd_last_vt == nil or vt ~= cmd_last_vt then
					cmd_vt_clock = now;
				end;
				cmd_last_vt = vt;
				local vt_alive = (now - cmd_vt_clock) < 1.0;
				if cmd_events == 1 or cmd_events % 200 == 0 or
						vt_alive ~= cmd_vt_alive then
					corel.log("PROBE telemetry cmd=" .. cmd_events ..
						" clock=" .. now ..
						" vanilla_ticks=" .. vt ..
						(vt_alive and "+" or " FROZEN") ..
						" pub_ticks=" .. tostring(rawget(_G, "aai_pub_ticks")));
				end;
				cmd_vt_alive = vt_alive;
			end;
		end;
		-- order capture: extract fields IN-HANDLER into plain strings.
		-- (2026-07-29: the 200-events-per-battle cap removed -- it silently
		-- blinded the stream mid-battle; the queue cap is the backpressure.)
		if cmd_armed and #cmd_queue < 100 then
			cmd_captured = cmd_captured + 1;
			local nm = "ERR";
			pcall(function()
				local x = event:get_name();
				if type(x) == "function" then
					local ok2, x2 = pcall(x);
					x = ok2 and x2 or nil;
				end;
				nm = type(x) .. ":" .. tostring(x);
			end);
			-- Gated get_unit capture (aai_attrib_on lever) -- retested SAFE
			-- 07-30 in isolation (reflect/API.md): store the userdata
			-- UNTOUCHED here, the pump drain reads name/type from its own
			-- legal context. Payload is per-event-type: real on Entity Hit,
			-- nil on "Special Ability" casts.
			local row_unit = nil;
			if attrib_armed then
				pcall(function()
					local u = event:get_unit();
					if u ~= nil then row_unit = u; end;
				end);
			end;
			local extra = {};
			for _, g in ipairs({ "get_bool1", "get_bool2", "get_string1",
					"get_string2", "get_float1" }) do
				pcall(function()
					local x = event[g](event);
					if type(x) == "function" then
						local ok2, x2 = pcall(x);
						x = ok2 and x2 or nil;
					end;
					if x ~= nil then
						extra[#extra + 1] = g .. "=" .. tostring(x);
					end;
				end);
			end;
			cmd_queue[#cmd_queue + 1] = { nm = nm,
				extra = table.concat(extra, " "), u = row_unit };
		end;
	end));
	local reg = false;
	local cmd_off = io.open("data/aai_cmd_off.txt", "r");
	if cmd_off then
		cmd_off:close();
		core.log("probe: command handler SKIPPED (data/aai_cmd_off.txt present)");
	else
		pcall(function() battle:register_command_handler("aai_probe_cmd"); reg = true; end);
		core.log("probe: command handler registered=" .. tostring(reg));
	end;

	-- phase flags (events must not touch the engine)
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

	-- Drive off publish's proven engine timer (a SECOND register_repeating_timer
	-- registers "ok" but never fires -- measured 2026-07-27). publish.lua calls
	-- this hook at the end of every pump tick (500ms); the divider below keeps
	-- probe logic at ~1s.
	rawset(_G, "aai_probe_tick", core.guarded("probe pump", pump));
	core.log("probe: hooked into publish pump (500ms host, /" .. DIVIDER ..
		" divider) gate=" .. FLAG);
end;

return M;
