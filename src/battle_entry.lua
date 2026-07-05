-------------------------------------------------------------------------
--	ATTILA-AI per-battle entry script. Attached to every campaign battle
--	by campaign/battle_script.lua via cm:add_custom_battlefield().
--
--	This chunk is the ONLY reliably-working context for engine interface
--	calls (enumeration calls like alliances:count() return garbage from
--	module chunks regardless of setfenv — measured 2026-07-04). So all
--	engine-facing operations live here, as closures exported through
--	rawset(_G, "aai_api", ...), and the enemy squad list is captured at
--	load. Modules contain logic only and call the bridge.
-------------------------------------------------------------------------

local function log(text)
	local f = io.open("data/attila_ai_log.txt", "a");
	if f then
		f:write("[battle+] " .. tostring(text) .. "\n");
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

	----------------------------------------------------------------
	--	bridge API — every closure here carries this chunk's
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

	api.move = function(uc, x, z, run)
		uc:goto_location(v(x, z), run and true or false);
	end;

	api.halt = function(uc)
		uc:halt();
	end;

	-- claim/return a unit from/to the battle AI. Without take_control
	-- the AI keeps issuing its own orders over ours (vanilla
	-- lib_script_ai_planner.lua documents this behaviour).
	api.take = function(uc)
		uc:take_control();
	end;

	api.release = function(uc)
		uc:release_control();
	end;

	-- enumerate every enemy unit, each with its own unit controller
	api.enum_enemy = function()
		local player_alliance = 1;
		pcall(function()
			local la = bm:local_alliance();
			if type(la) == "number" then
				player_alliance = la;
			end;
		end);
		local enemy_index = (player_alliance == 1) and 2 or 1;

		local squads = {};
		local armies = bm:alliances():item(enemy_index):armies();
		for a = 1, armies:count() do
			local army = armies:item(a);
			local units = army:units();
			for i = 1, units:count() do
				local unit = units:item(i);
				local uc = army:create_unit_controller();
				uc:add_units(unit);
				squads[#squads + 1] = { unit = unit, uc = uc };
			end;
		end;
		return squads, enemy_index;
	end;

	-- live squad cache: key "alliance:army:index" -> {unit, uc}.
	-- sync_squads() walks the CURRENT enemy lists and creates controllers
	-- for units it has not seen yet, so reinforcements that append to an
	-- army become controllable on the next sync. Keys never shift.
	local squad_cache = {};
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
		for m = 1, armies:count() do
			local army = armies:item(m);
			local units = army:units();
			for i = 1, units:count() do
				local key = enemy .. ":" .. m .. ":" .. i;
				if not squad_cache[key] then
					local unit = units:item(i);
					local uc = army:create_unit_controller();
					uc:add_units(unit);
					squad_cache[key] = { unit = unit, uc = uc };
					added = added + 1;
				end;
			end;
		end;
		return squad_cache, added;
	end;

	rawset(_G, "aai_api", api);

	api.battery("entry@load");

	local enum_ok, squads, enemy_index = pcall(api.enum_enemy);
	if enum_ok and type(squads) == "table" then
		rawset(_G, "aai_enemy_squads", squads);
		log("enemy squads captured at load: " .. #squads ..
			" units (alliance " .. tostring(enemy_index) .. ")");
	else
		log("enum_enemy at load FAILED: " .. tostring(squads));
	end;

	-- one-time API census: dump every method the engine exposes on the
	-- core battle objects (userdata metatable __index, or table keys)
	local function dump_methods(label, obj)
		local names = {};
		if type(obj) == "table" then
			for k in pairs(obj) do names[#names + 1] = tostring(k); end;
		end;
		local mt = getmetatable(obj);
		local idx = mt and mt.__index;
		if type(idx) == "table" then
			for k in pairs(idx) do names[#names + 1] = tostring(k); end;
		elseif idx then
			names[#names + 1] = "<__index is " .. type(idx) .. ">";
		end;
		table.sort(names);
		log("methods[" .. label .. "] (" .. #names .. "): " ..
			table.concat(names, " "));
	end;
	pcall(function()
		if enum_ok and squads[1] then
			dump_methods("unit", squads[1].unit);
			dump_methods("unit_controller", squads[1].uc);
		end;
		local alliance = bm:alliances():item(1);
		local army = alliance:armies():item(1);
		dump_methods("battle", bm.battle);
		dump_methods("alliance", alliance);
		dump_methods("army", army);
		dump_methods("units_list", army:units());
	end);

end;

package.path = package.path .. ";data/aai/?.lua";

local aai_ok, aai_err = pcall(require, "aai_battle_state");
if not aai_ok then
	log("ENTRY FAILED: " .. tostring(aai_err));
end;
