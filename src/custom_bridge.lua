-------------------------------------------------------------------------
--	ATTILA-AI custom-battle PAYLOAD (branch custom-battles, run 6).
--	Required by the aai_attach.lua KERNEL -- at bring-up and again on every
--	cockpit reload (the kernel clears package.loaded first, so this file is
--	re-read from disk; with dev copies in data/aai_dev/ that means edits
--	land mid-battle). Everything here must therefore be safe to run twice:
--	globals are rawset (overwrite), the module family below is re-required
--	fresh, and the kernel already truncated our old event handlers.
--
--	Content = run 5's bridge, verified live 2026-08-04: battle_entry.lua's
--	closures ported to the raw interface from empire_battle:new() (no CA
--	script libs exist in this world). Modules cannot tell this bridge from
--	the campaign one -- that equivalence is the whole point.
-------------------------------------------------------------------------
local M = {};

local battle = rawget(_G, "aai_raw_battle");
local P = rawget(_G, "aai_priv");

local function log(text)
	local f = io.open("data/aai_attach_chunk.txt", "a");
	if f then
		f:write("  [bridge] " .. tostring(text) .. "\n");
		f:close();
	end;
end;

if battle == nil or P == nil then
	log("ABORT: kernel state missing (aai_raw_battle/aai_priv)");
	error("custom_bridge: kernel state missing", 0);
end;

-- ---- vector constructor (replaces CA's v() helper) --------------------
local BV = rawget(P, "battle_vector");
local mkvec = nil;
pcall(function()
	local ok3, vec3 = pcall(function() return BV:new(1, 0, 2); end);
	if ok3 and type(vec3) == "userdata"
			and pcall(function() return vec3:get_x(); end)
			and vec3:get_x() == 1 then
		mkvec = function(x, z)
			return BV:new(x, 0, z);
		end;
		log("vector ctor = battle_vector:new(x, 0, z)");
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
			log("vector ctor = battle_vector:new() + set(x, 0, z)");
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
			log("vector ctor = battle_vector:new() + set_x/set_y/set_z");
			return;
		end;
	end;
	log("vector ctor: NO SHAPE TOOK -- position verbs will fail");
end);

----------------------------------------------------------------
--	the bridge API -- battle_entry.lua's closures on the raw interface
--	(bm: -> battle:). Same names + signatures as campaign.
----------------------------------------------------------------
local api = {};

api.battery = function(tag)
	local function t(label, fn)
		local ok, res = pcall(fn);
		log("battery[" .. tag .. "] " .. label .. " = " ..
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

-- Ownership laws unchanged from campaign: a take is not durable and does
-- not cancel the in-flight order -- callers re-assert per tick and follow
-- every take with a real order.
api.take = function(uc)
	uc:take_control();
end;

api.release = function(uc)
	uc:release_control();
end;

----------------------------------------------------------------
--	live squad cache: key "alliance:army:name" -> {unit, uc}.
--	Rebuilt from scratch on every (re)load -- keys are name-based and
--	stable, so the cockpit's addressing survives a reload unchanged.
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
			log("reinforcement arrived on field: " .. key);
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
					log("NAME COLLISION in army " .. rk .. ": two units named " ..
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
				log("roster " .. rk .. " n=" .. #names .. " names=" .. fp);
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
			log("sync: army " .. rk .. " walk FAILED: " .. tostring(err));
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

-- ---- publish the bridge contract --------------------------------------
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

local bless_env = setmetatable({ v = mkvec }, { __index = _G });
rawset(_G, "aai_bless", function(fn) return setfenv(fn, bless_env); end);
rawset(_G, "aai_env", getfenv(1));

api.battery("bridge@load");

-- ---- bring up the module stack ----------------------------------------
-- (module-family clearing + dev pre-stuff are the KERNEL's job -- by the
-- time this runs, package.loaded already holds the dev copies, so the
-- requires below and inside aai_battle_state hit the cache. NEVER touch
-- package.loaders/require here: shared engine plumbing, see the kernel.)
if not string.find(package.path, "data/aai/?.lua", 1, true) then
	package.path = package.path .. ";data/aai/?.lua";
end;

-- the orchestrator runs code at top level, so it loads like the payload
-- itself: dev copy via stdio when present, else the pack
local ok, err;
local fh = io.open("data/aai_dev/aai_battle_state.lua", "r");
if fh then
	local src = fh:read("*a");
	fh:close();
	local chunk, cerr = loadstring(src, "@data/aai_dev/aai_battle_state.lua");
	if chunk then
		ok, err = pcall(chunk);
		if ok then
			pcall(function() package.loaded["aai_battle_state"] = true; end);
		end;
	else
		ok, err = false, cerr;
	end;
else
	ok, err = pcall(require, "aai_battle_state");
end;
if not ok then
	log("MODULE STACK FAILED: " .. tostring(err));
	error("custom_bridge: module stack failed: " .. tostring(err), 0);
end;
log("module stack up");

return M;
