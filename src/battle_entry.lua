-------------------------------------------------------------------------
--	ATTILA-AI per-battle entry script. Attached to every campaign battle
--	by campaign/battle_script.lua via cm:add_custom_battlefield().
--
--	This chunk is the ONLY reliably-working context for engine interface
--	calls (enumeration calls like alliances:count() return garbage from
--	module chunks regardless of setfenv -- measured 2026-07-04). So all
--	engine-facing operations live here, as closures exported through
--	rawset(_G, "aai_api", ...). Modules contain logic only and call the
--	bridge.
-------------------------------------------------------------------------

local function log(text)
	local f = io.open("data/attila_ai_log.txt", "a");
	if f then
		local ts = "";
		pcall(function() ts = os.date("%H:%M:%S") .. " "; end);
		f:write("[battle+] " .. ts .. tostring(text) .. "\n");
		f:close();
	end;
end;

log("");
log("==== battle entry script running (custom battlefield hook) ====");

local hdr_ok, hdr_err = pcall(require, "lua_scripts.Battle_Script_Header");
if not hdr_ok then
	log("Battle_Script_Header FAILED: " .. tostring(hdr_err));
end;

local bm_ok, bm = pcall(function() return get_bm(); end);
if not (bm_ok and bm) then
	log("get_bm() FAILED: " .. tostring(bm));
else
	rawset(_G, "aai_bm", bm);
	rawset(_G, "aai_env", getfenv(1));
	rawset(_G, "aai_bless", function(fn) return setfenv(fn, getfenv(1)); end);

	-- battles with prepare_for_fade_in start black and trust the battle
	-- script to fade in; harmless no-op everywhere else
	pcall(function() bm:camera():fade(false, 1); end);

	log("battle manager acquired");

	local ok_id, batid = pcall(function() return os.date("%Y%m%d_%H%M%S"); end);
	rawset(_G, "aai_battle_id", (ok_id and batid) or "battle");

	----------------------------------------------------------------
	--	bridge API -- every closure here carries this chunk's
	--	environment and works where module code does not
	----------------------------------------------------------------
	local api = {};

	-- science: same four calls from different execution contexts
	api.battery = function(tag)
		local function t(label, fn)
			local ok, res = pcall(fn);
			log("battery[" .. tag .. "] " .. label .. " = " ..
				(ok and (type(res) .. ": " .. tostring(res)) or ("ERROR " .. tostring(res))));
		end;
		t("local_alliance", function() return bm:local_alliance(); end);
		t("alliances:count", function() return bm:alliances():count(); end);
		t("armies:count", function() return bm:alliances():item(2):armies():count(); end);
		t("units:count", function()
			return bm:alliances():item(2):armies():item(1):units():count();
		end);
	end;

	api.unit_pos = function(unit)
		local p = unit:position();
		return p:get_x(), p:get_z(), unit:bearing();
	end;

	-- the engine's current commanded destination for the unit (x, z),
	-- whoever issued it -- our watchdog reads this to spot AI overrides
	api.unit_ordered = function(unit)
		local p = unit:ordered_position();
		if not p then
			return nil;
		end;
		return p:get_x(), p:get_z();
	end;

	-- ONLY verified methods here: is_in_melee() does not exist (zero
	-- hits in all of reference/; calling it threw on every unit and
	-- silently disabled the whole order path on 2026-07-06)
	api.unit_flags = function(unit)
		return (unit:is_routing() and true or false),
			(unit:is_shattered() and true or false);
	end;

	api.move = function(uc, x, z, run)
		uc:goto_location(v(x, z), run and true or false);
	end;

	api.halt = function(uc)
		uc:halt();
	end;

	-- Ownership. take_control moves a unit from its default owner (the
	-- battle AI, for non-player armies) to script. Two hard-won facts
	-- (2026-07-05, from reference/vanilla + live logs):
	--  * one take is NOT durable -- vanilla re-takes before every order
	--    (lib_patrol_manager.lua:518); callers must re-assert per tick.
	--  * a bare take does NOT cancel the unit's in-flight order -- a
	--    claimed army kept executing the AI's opening advance; follow
	--    every take with a real order (halt/goto).
	api.take = function(uc)
		uc:take_control();
	end;

	api.release = function(uc)
		uc:release_control();
	end;

	----------------------------------------------------------------
	--	live squad cache: key "alliance:army:name" -> {unit, uc}.
	--	unit:name() in campaign battles is the per-army spawn ordinal
	--	("1".."N"), unique and stable as reinforcements append (vanilla
	--	uses names as identities: lib_script_unit.lua:86). sync_squads
	--	walks the CURRENT deployed list plus get_reinforcement_units()
	--	(vanilla pre-adds those too: lib_misc_battle.lua:105) and makes
	--	a controller per new unit. A roster fingerprint is logged per
	--	army whenever it changes, to expose name/index instability.
	----------------------------------------------------------------
	local squad_cache = {};
	local roster_seen = {};
	local name_warned = {};
	local army_fail_logged = {};

	local function unit_id(unit)
		-- whitespace would break the orders-file line format; the viz
		-- JS applies the identical substitution
		return string.gsub(tostring(unit:name()), "%s", "_");
	end;

	local function cache_unit(enemy, m, army, unit, pre)
		local key = enemy .. ":" .. m .. ":" .. unit_id(unit);
		local entry = squad_cache[key];
		if entry then
			if entry.pre and not pre then
				entry.pre = nil;	-- pre-cached earlier; now on the field
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
			local la = bm:local_alliance();
			if type(la) == "number" then
				player_alliance = la;
			end;
		end);
		local enemy = (player_alliance == 1) and 2 or 1;
		local added = 0;
		local armies = bm:alliances():item(enemy):armies();
		local acount = armies:count();		-- non-number early in sieges -> retry next tick
		if type(acount) ~= "number" then acount = 0; end;
		for m = 1, acount do
			local rk = enemy .. ":" .. m;
			local ok, err = pcall(function()
				local army = armies:item(m);
				local units = army:units();
				local names = {};
				local seen = {};
				local men0s = {};
				local ucount = units:count();
				if type(ucount) ~= "number" then ucount = 0; end;
				for i = 1, ucount do
					local unit = units:item(i);
					local nm = unit_id(unit);
					local m0 = nil;
					pcall(function() m0 = unit:initial_number_of_men(); end);
					men0s[#men0s + 1] = tostring(m0);
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
					log("roster " .. rk .. " men0=" .. table.concat(men0s, ","));
				end;
				-- pre-cache reinforcements not yet on the field, so they
				-- can be claimed the moment (or before) they arrive. The
				-- list may also carry names already deployed (arrival
				-- semantics unconfirmed); cross-list dedupe is cache_unit's
				-- key check, so only dedupe within this list here.
				pcall(function()
					local runits = army:get_reinforcement_units();
					local rseen = {};
					local unnamed = 0;
					for i = 1, runits:count() do
						local unit = runits:item(i);
						local nm = unit_id(unit);
						-- pre-arrival units have EMPTY names (measured
						-- 2026-07-06) -- they only become addressable
						-- when they deploy, so skip them here
						if nm == "" then
							unnamed = unnamed + 1;
						elseif not rseen[nm] then
							rseen[nm] = true;
							local key = cache_unit(enemy, m, army, unit, true);
							if key then
								added = added + 1;
								log("pre-cached reinforcement unit " .. key);
							end;
						end;
					end;
					if unnamed > 0 and not name_warned[rk .. ":unnamed"] then
						name_warned[rk .. ":unnamed"] = true;
						log("army " .. rk .. ": " .. unnamed ..
							" reinforcement units unnamed pre-arrival -- will claim on deploy");
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

	-- resolve a live unit object to its STABLE cache key (assigned at first
	-- sight, immune to unit:name() renumbering after casualties); matched by
	-- engine object identity (__eq). state_json exports this so the page's
	-- key IS the cache key, not a name recomputed each frame.
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
		uc:goto_location_angle_width(v(x, z), bearing, width, run and true or false);
	end;

	api.attack_pos = function(uc, x, z, run)
		uc:attack_location(v(x, z), run and true or false);
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
		uc:occupy_zone(v(x, z), run and true or false);
	end;

	api.teleport = function(uc, x, z, bearing, width)
		uc:teleport_to_location(v(x, z), bearing, width);
	end;

	api.change_fatigue = function(uc, n)
		uc:change_fatigue_amount(n);
	end;

	-- destroy the building nearest (x,z): scripted stand-in for a ram / an
	-- artillery hit on a wall/gate (no discrete attack-building command exists).
	api.attack_building = function(x, z)
		local b = bm:get_building_near(x, z);
		if b then
			b:destroy();
		end;
	end;

	-- Enumerate TACTICAL buildings (walls/gates/towers/barricades) for the
	-- write-once geometry file: {x, z, n=name}. bm:buildings() returns EVERY
	-- prop on the map -- measured 16,149 on a city map (vines_pillar 4901,
	-- archerytarget 2534, lowfence_3 1800, crates, barrels, rocks...). That
	-- noise is useless to an AI and bloats the feed, so filter hard and cap.
	-- (A naive "wall|gate|tower" match is NOT enough: it catches lowfence_*.)
	local BLD_CAP = 2000;	-- mirrored by GEOM_CAP in battle/publish.lua
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
		local blds = bm:buildings();
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

	-- attack the player-alliance unit nearest to (x, z). We only cache the
	-- enemy army we puppet, so the player targets are enumerated live here.
	api.attack_unit_near = function(attacker_uc, x, z)
		local pa = 1;
		pcall(function()
			local la = bm:local_alliance();
			if type(la) == "number" then pa = la; end;
		end);
		local best, bestd = nil, nil;
		local armies = bm:alliances():item(pa):armies();
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

	-- one-shot probes (logged). Two kinds:
	--  * FIELD-probe: read a method field's TYPE without calling (safe even for
	--    actuators) -> "function"/"nil".
	--  * VALUE-probe: CALL a read-only predicate/getter and log its value; only
	--    used for is_*/unit_class/behaviour/getter names -- no side effects.
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
		-- blind-sibling read predicates (call; read-only by name)
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
		-- which behaviours/abilities report active on this unit right now
		local behs = {"fire_at_will", "change_formation_spacing", "skirmish",
			"guard_mode", "phalanx", "shield_wall", "schiltrom", "charge",
			"hide_in_forest", "hide_anywhere", "special_ability"};
		for _, b in ipairs(behs) do
			local ok, val = pcall(function() return unit:is_behaviour_active(b); end);
			logf("PROBE beh is_behaviour_active(" .. b .. ") -> " ..
				(ok and tostring(val) or "ERR"));
		end;
	end;

	-- battlefield-level blind probes: weather / capture points / buildings.
	api.probe_battle = function(logf)
		local getters = {"weather", "get_weather", "current_weather", "weather_type",
			"time_of_day", "fort_plazas", "capture_locations", "capture_points",
			"victory_locations", "plazas", "deployment_areas"};
		for _, nm in ipairs(getters) do
			local ok, val = pcall(function() return bm[nm](bm); end);
			logf("PROBE bm:" .. nm .. "() -> " ..
				(ok and (type(val) .. ":" .. tostring(val)) or ("ERR " .. tostring(val))));
		end;
		pcall(function()
			local blds = bm:buildings();
			local n = blds:count();
			logf("PROBE bm:buildings():count() = " .. tostring(n));
			if n and n > 0 then
				local b = blds:item(1);
				local bg = {"is_on_fire", "is_destroyed", "health", "is_intact",
					"name", "central_position"};
				for _, nm in ipairs(bg) do
					local ok, val = pcall(function() return b[nm](b); end);
					logf("PROBE building:" .. nm .. "() -> " ..
						(ok and (type(val) .. ":" .. tostring(val)) or ("ERR " .. tostring(val))));
				end;
			end;
		end);
	end;

	rawset(_G, "aai_api", api);

	api.battery("entry@load");
end;

package.path = package.path .. ";data/aai/?.lua";

local aai_ok, aai_err = pcall(require, "aai_battle_state");
if not aai_ok then
	log("ENTRY FAILED: " .. tostring(aai_err));
end;
