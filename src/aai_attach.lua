-------------------------------------------------------------------------
--	ATTILA-AI attached chunk (branch custom-battles, Door B Route A).
--	NOT a module: the ENGINE loads and runs this by name into the script
--	interface, once the attach gate sees a non-empty BATTLE+0x64128.
--
--	RUN 5: THE PRODUCTION BRIDGE. Runs 1-4 proved the whole chain (attach,
--	privileged table, empire_battle:new(), live roster reads). This run
--	ports battle_entry.lua's bridge to this environment and brings up the
--	full external stack: aai_api closures + aai_bm shim -> require
--	"aai_battle_state" (api + publish + probe + harness) -> the cockpit
--	feed (data/aai_battle.json) and the harness mailbox work exactly as
--	in campaign battles.
--
--	Environment facts this port is built on (all live-verified, runs 1-4):
--	  * We execute on the BOOTSTRAP globals table -- shared package.path
--	    (data/aai/?.lua), shared require/_LOADED, shared `events` table
--	    (registrations fire, 262/264), no sandbox.
--	  * No CA script library exists here: no get_bm, no battle_manager, no
--	    v(), no tick_increment_counter. Everything must build on the raw
--	    interface from empire_battle:new() (colon form; dot form -> nil).
--	  * Engine reads work even at chunk-load time here (run 4 walked the
--	    full roster at load) -- unlike campaign module chunks.
--
--	THE PUMP (the one new mechanism): campaign publish rides vanilla's
--	100ms timer because registering a SECOND script timer kills the whole
--	dispatch (timer law, SCRIPTING.md). In a custom battle NOTHING has
--	registered a timer -- CA's libs never ran -- so our single
--	register_repeating_timer mirrors exactly what vanilla's own
--	lib_timer_manager does at bm creation, and is the only tick source
--	available. Lever data/aai_no_ctimer.txt skips the registration for
--	bisection. Needs Theo's live verification like everything else.
-------------------------------------------------------------------------

local LOG = "data/aai_attach_chunk.txt";
local BUDGET = 400;

local lines = 0;
local function w(line)
	if lines >= BUDGET then
		return;
	end;
	lines = lines + 1;
	local f = io.open(LOG, "a");
	if f then
		f:write(tostring(line) .. "\n");
		f:close();
	end;
end;

local function try(tag, fn)
	local ok, err = pcall(fn);
	if not ok then
		w("  ERROR in " .. tag .. ": " .. tostring(err));
	end;
	return ok;
end;

-- re-entry guard: the engine one-shot loads us, but A:reload_battle_script
-- re-runs the file; a second bring-up would double event handlers + timers
if rawget(_G, "aai_stack_up") then
	w("==== aai_attach.lua re-entry: stack already up, nothing to do ====");
	return;
end;

w("");
w("==== aai_attach.lua RUN 5 -- FULL STACK ====");
try("time", function() w("time: " .. os.date("%Y-%m-%d %H:%M:%S")); end);

-- ---- 1. recover the privileged table (proven route, runs 3-4) ---------
local P = nil;
try("find-env", function()
	local dbg = rawget(_G, "debug");
	if type(dbg) ~= "table" or type(dbg.getregistry) ~= "function" then
		return;
	end;
	local reg = dbg.getregistry();
	local function consider(t)
		if P or type(t) ~= "table" then
			return;
		end;
		local ok, v = pcall(rawget, t, "empire_battle");
		if ok and v ~= nil then
			P = t;
		end;
	end;
	for _, v in pairs(reg) do
		consider(v);
		if type(v) == "thread" then
			for lvl = 0, 12 do
				local ok, info = pcall(dbg.getinfo, v, lvl, "f");
				if not ok or type(info) ~= "table" or info.func == nil then
					break;
				end;
				local ok2, env = pcall(getfenv, info.func);
				if ok2 then
					consider(env);
				end;
			end;
		end;
		if P then
			break;
		end;
	end;
	if not P then
		consider(reg);
	end;
	w("  privileged table = " .. tostring(P));
end);

if not P then
	w("  ABORT: could not reach the privileged table this run");
	w("==== end RUN 5 ====");
	return;
end;

-- ---- 2. instantiate the battle interface ------------------------------
local EB = rawget(P, "empire_battle");
local battle = nil;
try("instantiate", function()
	battle = EB:new();		-- colon form required (run 4: dot form -> nil)
end);
w("  empire_battle:new() -> " .. tostring(battle));

if battle == nil then
	w("  ABORT: no interface instance");
	w("==== end RUN 5 ====");
	return;
end;

-- ---- 3. vector constructor (replaces CA's v() helper) -----------------
-- battle_vector carries `new` in its __index (run 3); reflect/API.md lists
-- set(x,y,z). Try the ctor shapes cheapest-first and log which one took.
local BV = rawget(P, "battle_vector");
local mkvec = nil;
try("vector", function()
	local ok3, vec3 = pcall(function() return BV:new(1, 0, 2); end);
	if ok3 and type(vec3) == "userdata"
			and pcall(function() return vec3:get_x(); end)
			and vec3:get_x() == 1 then
		mkvec = function(x, z)
			return BV:new(x, 0, z);
		end;
		w("  vector ctor = battle_vector:new(x, 0, z)");
		return;
	end;
	local ok0, vec0 = pcall(function() return BV:new(); end);
	if ok0 and type(vec0) == "userdata" then
		local okset = pcall(function() vec0:set(1, 0, 2); end);
		if okset then
			mkvec = function(x, z)
				local vv = BV:new();
				vv:set(x, 0, z);
				return vv;
			end;
			w("  vector ctor = battle_vector:new() + set(x, 0, z)");
			return;
		end;
		local okxyz = pcall(function()
			vec0:set_x(1); vec0:set_y(0); vec0:set_z(2);
		end);
		if okxyz then
			mkvec = function(x, z)
				local vv = BV:new();
				vv:set_x(x); vv:set_y(0); vv:set_z(z);
				return vv;
			end;
			w("  vector ctor = battle_vector:new() + set_x/set_y/set_z");
			return;
		end;
	end;
	w("  vector ctor: NO SHAPE TOOK -- position verbs will fail");
end);

----------------------------------------------------------------
--	4. the bridge API -- battle_entry.lua's closures ported to the raw
--	interface (bm: -> battle:). Same names + signatures: battle/api.lua,
--	battle/publish.lua, battle/harness.lua and battle/probe.lua call
--	these and must not be able to tell the two environments apart.
----------------------------------------------------------------
local api = {};

api.battery = function(tag)
	local function t(label, fn)
		local ok, res = pcall(fn);
		w("  battery[" .. tag .. "] " .. label .. " = " ..
			(ok and (type(res) .. ": " .. tostring(res)) or ("ERROR " .. tostring(res))));
	end;
	t("local_alliance", function() return battle:local_alliance(); end);
	t("alliances:count", function() return battle:alliances():count(); end);
	t("armies:count", function() return battle:alliances():item(2):armies():count(); end);
	t("units:count", function()
		return battle:alliances():item(2):armies():item(1):units():count();
	end);
end;

api.unit_pos = function(unit)
	local p = unit:position();
	return p:get_x(), p:get_z(), unit:bearing();
end;

api.unit_ordered = function(unit)
	local p = unit:ordered_position();
	if not p then
		return nil;
	end;
	return p:get_x(), p:get_z();
end;

api.unit_flags = function(unit)
	return (unit:is_routing() and true or false),
		(unit:is_shattered() and true or false);
end;

api.move = function(uc, x, z, run)
	uc:goto_location(mkvec(x, z), run and true or false);
end;

api.halt = function(uc)
	uc:halt();
end;

-- Ownership laws are unchanged from campaign (battle_entry.lua): a take is
-- not durable and does not cancel the in-flight order -- callers re-assert
-- per tick and follow every take with a real order.
api.take = function(uc)
	uc:take_control();
end;

api.release = function(uc)
	uc:release_control();
end;

----------------------------------------------------------------
--	live squad cache: key "alliance:army:name" -> {unit, uc}
--	(verbatim from battle_entry.lua apart from bm: -> battle:)
----------------------------------------------------------------
local squad_cache = {};
local roster_seen = {};
local name_warned = {};
local army_fail_logged = {};

local function unit_id(unit)
	return string.gsub(tostring(unit:name()), "%s", "_");
end;

local function cache_unit(enemy, m, army, unit, pre)
	local key = enemy .. ":" .. m .. ":" .. unit_id(unit);
	local entry = squad_cache[key];
	if entry then
		if entry.pre and not pre then
			entry.pre = nil;
			w("  reinforcement arrived on field: " .. key);
		end;
		return nil;
	end;
	local uc = army:create_unit_controller();
	uc:add_units(unit);
	squad_cache[key] = { unit = unit, uc = uc, pre = pre or nil };
	return key;
end;

api.sync_squads = function()
	local player_alliance = 1;
	pcall(function()
		local la = battle:local_alliance();
		if type(la) == "number" then
			player_alliance = la;
		end;
	end);
	local enemy = (player_alliance == 1) and 2 or 1;
	local added = 0;
	local armies = battle:alliances():item(enemy):armies();
	local acount = armies:count();
	if type(acount) ~= "number" then acount = 0; end;
	for m = 1, acount do
		local rk = enemy .. ":" .. m;
		local ok, err = pcall(function()
			local army = armies:item(m);
			local units = army:units();
			local names = {};
			local seen = {};
			local ucount = units:count();
			if type(ucount) ~= "number" then ucount = 0; end;
			for i = 1, ucount do
				local unit = units:item(i);
				local nm = unit_id(unit);
				if seen[nm] and not name_warned[rk .. ":" .. nm] then
					name_warned[rk .. ":" .. nm] = true;
					w("  NAME COLLISION in army " .. rk .. ": two units named " ..
						nm .. " -- the second is uncontrollable");
				end;
				seen[nm] = true;
				names[#names + 1] = nm;
				if cache_unit(enemy, m, army, unit) then
					added = added + 1;
				end;
			end;
			local fp = table.concat(names, ",");
			if roster_seen[rk] ~= fp then
				roster_seen[rk] = fp;
				w("  roster " .. rk .. " n=" .. #names .. " names=" .. fp);
			end;
			pcall(function()
				local runits = army:get_reinforcement_units();
				local rseen = {};
				for i = 1, runits:count() do
					local unit = runits:item(i);
					local nm = unit_id(unit);
					-- pre-arrival units have EMPTY names -- addressable
					-- only once deployed
					if nm ~= "" and not rseen[nm] then
						rseen[nm] = true;
						if cache_unit(enemy, m, army, unit, true) then
							added = added + 1;
						end;
					end;
				end;
			end);
			army_fail_logged[rk] = nil;
		end);
		if not ok and not army_fail_logged[rk] then
			army_fail_logged[rk] = true;
			w("  sync: army " .. rk .. " walk FAILED: " .. tostring(err));
		end;
	end;
	return squad_cache, added;
end;

api.unit_key = function(unit)
	for key, entry in pairs(squad_cache) do
		local ok, same = pcall(function() return entry.unit == unit; end);
		if ok and same then
			return key;
		end;
	end;
	return nil;
end;

-- ---- command closures (verified unit_controller methods) --------
api.form = function(uc, x, z, bearing, width, run)
	uc:goto_location_angle_width(mkvec(x, z), bearing, width, run and true or false);
end;

api.attack_pos = function(uc, x, z, run)
	uc:attack_location(mkvec(x, z), run and true or false);
end;

api.withdraw = function(uc, run)
	uc:withdraw(run and true or false);
end;

api.fire_at_will = function(uc, on)
	uc:fire_at_will(on and true or false);
end;

api.melee = function(uc, on)
	uc:melee(on and true or false);
end;

api.walk_speed = function(uc, mult)
	uc:change_current_walk_speed(mult);
end;

api.shot_type = function(uc, name)
	uc:change_shot_type(name);
end;

api.occupy_zone = function(uc, x, z, run)
	uc:occupy_zone(mkvec(x, z), run and true or false);
end;

api.teleport = function(uc, x, z, bearing, width)
	uc:teleport_to_location(mkvec(x, z), bearing, width);
end;

api.change_fatigue = function(uc, n)
	uc:change_fatigue_amount(n);
end;

api.attack_building = function(x, z)
	-- get_building_near is a documented phantom; kept so issue() acks the
	-- same ERR as campaign rather than "unknown action"
	local b = battle:get_building_near(x, z);
	if b then
		b:destroy();
	end;
end;

-- tactical-building filter for the write-once geometry file (verbatim from
-- battle_entry.lua; only reachable behind the aai_geom_on.txt lever --
-- the buildings surface stays quarantined, SCRIPTING.md)
local BLD_CAP = 2000;
local BLD_DROP = { "lowfence", "vines", "archery", "crate", "barrel",
	"wheelbarrow", "rock", "pathborder", "ground", "bush", "tree", "cart",
	"sack", "amphora", "shack", "rubble", "debris" };
local BLD_KEEP = { "wall", "gate", "tower", "rampart", "palis", "barricade",
	"keep", "fort", "door" };
local function bld_tactical(n)
	if not n or n == "" then
		return false;
	end;
	local low = string.lower(n);
	for _, d in ipairs(BLD_DROP) do
		if string.find(low, d, 1, true) then
			return false;
		end;
	end;
	for _, k in ipairs(BLD_KEEP) do
		if string.find(low, k, 1, true) then
			return true;
		end;
	end;
	return false;
end;

api.buildings = function()
	local out = {};
	local blds = battle:buildings();
	if not blds then
		return out;
	end;
	local cnt = blds:count();
	if type(cnt) ~= "number" then
		return out;
	end;
	for i = 1, cnt do
		if #out >= BLD_CAP then
			break;
		end;
		local b = blds:item(i);
		if b then
			local nm = nil;
			pcall(function() nm = tostring(b:name()); end);
			if bld_tactical(nm) then
				local entry = { n = nm };
				pcall(function()
					local p = b:central_position();
					entry.x = p:get_x();
					entry.z = p:get_z();
				end);
				if entry.x then
					out[#out + 1] = entry;
				end;
			end;
		end;
	end;
	return out;
end;

api.morale = function(uc, mode)
	if mode == "rout" then
		uc:morale_behavior_rout();
	elseif mode == "fearless" then
		uc:morale_behavior_fearless();
	else
		uc:morale_behavior_default();
	end;
end;

api.kill = function(uc)
	uc:kill();
end;

api.taunt = function(uc)
	uc:start_taunting();
end;

api.ability = function(uc, id)
	uc:perform_special_ability(id);
end;

api.attack_unit_near = function(attacker_uc, x, z)
	local pa = 1;
	pcall(function()
		local la = battle:local_alliance();
		if type(la) == "number" then pa = la; end;
	end);
	local best, bestd = nil, nil;
	local armies = battle:alliances():item(pa):armies();
	for m = 1, armies:count() do
		local units = armies:item(m):units();
		for i = 1, units:count() do
			local u = units:item(i);
			local ok, dd = pcall(function()
				local p = u:position();
				local dx, dz = p:get_x() - x, p:get_z() - z;
				return dx * dx + dz * dz;
			end);
			if ok and dd and (not bestd or dd < bestd) then
				bestd = dd;
				best = u;
			end;
		end;
	end;
	if best then
		attacker_uc:attack_unit(best, true, true);
	end;
end;

api.probe_unit = function(unit, uc, logf)
	local field_names = {"special_abilities", "abilities", "special_ability",
		"activate_special_ability", "use_special_ability", "activate_ability"};
	for _, nm in ipairs(field_names) do
		local ty, tc = "err", "err";
		pcall(function() ty = type(unit[nm]); end);
		pcall(function() tc = type(uc[nm]); end);
		logf("PROBE field unit." .. nm .. "=" .. tostring(ty) ..
			" uc." .. nm .. "=" .. tostring(tc));
	end;
	local preds = {"is_infantry", "is_missile", "is_spear", "is_pike",
		"is_elephant", "is_chariot", "is_mounted", "is_general", "is_commander",
		"is_leader", "is_in_melee", "is_charging", "is_flanked", "is_hidden",
		"is_deployed", "is_moving_fast", "is_under_missile_attack", "unit_class",
		"unit_key", "morale", "number_of_men_in_melee", "starting_ammo"};
	for _, nm in ipairs(preds) do
		local ok, val = pcall(function() return unit[nm](unit); end);
		logf("PROBE read unit:" .. nm .. "() -> " ..
			(ok and (type(val) .. ":" .. tostring(val)) or ("ERR " .. tostring(val))));
	end;
	local behs = {"fire_at_will", "change_formation_spacing", "skirmish",
		"guard_mode", "phalanx", "shield_wall", "schiltrom", "charge",
		"hide_in_forest", "hide_anywhere", "special_ability"};
	for _, b in ipairs(behs) do
		local ok, val = pcall(function() return unit:is_behaviour_active(b); end);
		logf("PROBE beh is_behaviour_active(" .. b .. ") -> " ..
			(ok and tostring(val) or "ERR"));
	end;
end;

api.probe_battle = function(logf)
	local getters = {"weather", "get_weather", "current_weather", "weather_type",
		"time_of_day", "fort_plazas", "capture_locations", "capture_points",
		"victory_locations", "plazas", "deployment_areas"};
	for _, nm in ipairs(getters) do
		local ok, val = pcall(function() return battle[nm](battle); end);
		logf("PROBE battle:" .. nm .. "() -> " ..
			(ok and (type(val) .. ":" .. tostring(val)) or ("ERR " .. tostring(val))));
	end;
end;

-- ---- 5. publish the bridge contract -----------------------------------
-- aai_bm: battle/api.lua reads .battle; harness's bldstep + battery-style
-- colon calls forward to the raw interface via the metatable
local bmshim = { battle = battle };
setmetatable(bmshim, {
	__index = function(t, k)
		return function(self, ...)
			return battle[k](battle, ...);
		end;
	end,
});
rawset(_G, "aai_bm", bmshim);
rawset(_G, "aai_api", api);

-- aai_bless: campaign modules use it to reach the entry env's v(). Here
-- everything shares one globals table, so blessing = an env that adds v.
local bless_env = setmetatable({ v = mkvec }, { __index = _G });
rawset(_G, "aai_bless", function(fn) return setfenv(fn, bless_env); end);
rawset(_G, "aai_env", getfenv(1));

local ok_id, batid = pcall(function() return os.date("%Y%m%d_%H%M%S"); end);
rawset(_G, "aai_battle_id", (ok_id and batid) or "battle");
rawset(_G, "aai_tick_count", 0);

api.battery("attach@load");

-- ---- 6. bring up the module stack (api + publish + probe + harness) ---
package.path = package.path .. ";data/aai/?.lua";

-- defensive: fresh module state even if something in this shared state
-- already required part of the family (idempotent when entries are nil)
try("module-reset", function()
	if type(package) == "table" and type(package.loaded) == "table" then
		for _, name in ipairs({ "aai_battle_state", "battle/api",
				"battle/publish", "battle/probe", "battle/harness",
				"battle/native", "battle/db", "battle/db_abilities" }) do
			package.loaded[name] = nil;
		end;
	end;
end);

local aai_ok, aai_err = pcall(require, "aai_battle_state");
w("  require aai_battle_state -> " .. (aai_ok and "OK" or tostring(aai_err)));
if not aai_ok then
	w("==== end RUN 5 (stack failed) ====");
	return;
end;

-- ---- 7. THE PUMP: our single engine timer (see header) ----------------
local no_timer = io.open("data/aai_no_ctimer.txt", "r");
if no_timer then
	no_timer:close();
	w("  pump timer SKIPPED (data/aai_no_ctimer.txt) -- feed will be dead");
else
	local tick_n = 0;
	rawset(_G, "aai_custom_tick", function()
		-- runs INSIDE the engine's timer dispatch: an uncaught error here
		-- is a crash, so the whole body is pcall'd
		pcall(function()
			tick_n = tick_n + 1;
			rawset(_G, "aai_tick_count", tick_n);
			if tick_n <= 3 or tick_n % 300 == 0 then
				w("  custom tick " .. tick_n .. " clock=" .. tostring(os.clock()));
			end;
			-- 2s warm-up keeps the first pumps out of the load window;
			-- publish's pump_core rate-limits 100ms kicks to ~500ms
			if tick_n >= 20 then
				local k = rawget(_G, "aai_pub_kick");
				if k then
					k("ctimer");
				end;
			end;
		end);
	end);
	local okt, errt = pcall(function()
		battle:register_repeating_timer("aai_custom_tick", 100);
	end);
	w("  register_repeating_timer(aai_custom_tick, 100) -> " ..
		(okt and "OK" or ("ERR " .. tostring(errt))));
end;

rawset(_G, "aai_stack_up", true);
w("==== RUN 5 bring-up complete: watch attila_ai_log.txt + the cockpit ====");
