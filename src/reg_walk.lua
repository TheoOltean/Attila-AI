-------------------------------------------------------------------------
--	ATTILA-AI registry walk (branch custom-battles, Door B rung 1).
--	From a bootstrap VM (no battle interface), debug.getregistry() may
--	still reach the engine's attach machinery: the injected-environment
--	tables, the interface userdata metatables, or attached-chunk
--	functions whose getfenv IS the privileged environment. This module
--	censuses the registry's shape, then needle-hunts all of those.
--	Listed by the battle and frontend orchestrators; core.world tags
--	the lines.
--
--	DEFAULT-OFF: self-gates on data/aai_reg_walk.txt.
-------------------------------------------------------------------------

local M = {};

-- globals only the injected battle environment carries
local ENV_NAMES = { "empire_battle", "battle_manager", "get_bm",
	"tick_increment_counter", "battle_setup_info", "UIComponent",
	"add_custom_battlefield" };
-- distinctive members of the battle-interface method tables
local METHOD_NAMES = { "number_of_men_alive", "missile_range",
	"unit_distance", "change_shot_type", "slot_list", "alliances" };

local NODE_BUDGET = 25000;
local MAX_DEPTH = 6;

function M.init(core)
	local f = io.open("data/aai_reg_walk.txt", "r");
	if not f then
		return;
	end;
	f:close();

	local dbg = rawget(_G, "debug");
	if type(dbg) ~= "table" or type(dbg.getregistry) ~= "function" then
		core.log("REG-WALK ABORT: debug.getregistry unavailable in the "
			.. core.world .. " VM");
		return;
	end;
	local reg = dbg.getregistry();
	core.log("REG-WALK start (" .. core.world .. " VM)");

	-- 1. registry shape: entry count by key/value type, all string keys
	local nkeys, vtypes, skeys = 0, {}, {};
	for k, v in pairs(reg) do
		nkeys = nkeys + 1;
		local vt = type(v);
		vtypes[vt] = (vtypes[vt] or 0) + 1;
		if type(k) == "string" then
			skeys[#skeys + 1] = k;
		end;
	end;
	table.sort(skeys);
	local shape = {};
	for vt, n in pairs(vtypes) do
		shape[#shape + 1] = vt .. "=" .. n;
	end;
	core.log("REG-WALK shape: " .. nkeys .. " entries ("
		.. table.concat(shape, " ") .. "), " .. #skeys .. " string keys");
	local line = {};
	for i = 1, math.min(#skeys, 400) do
		line[#line + 1] = skeys[i];
		if #line == 8 or i == #skeys or i == 400 then
			core.log("REG-WALK   key " .. table.concat(line, " "));
			line = {};
		end;
	end;
	if #skeys > 400 then
		core.log("REG-WALK   (" .. (#skeys - 400) .. " more string keys cut)");
	end;

	-- loaded module names are a cheap census of what runs in this VM
	local loaded = reg._LOADED;
	if type(loaded) == "table" then
		local names = {};
		for k in pairs(loaded) do
			names[#names + 1] = tostring(k);
		end;
		table.sort(names);
		core.log("REG-WALK _LOADED: " .. table.concat(names, " "));
	end;

	-- 2. the hunt: walk tables/metatables/function-envs, needle-check keys
	local visited, nodes, hits = {}, 0, 0;

	local function check_table(t, path)
		for i = 1, #ENV_NAMES do
			if rawget(t, ENV_NAMES[i]) ~= nil then
				hits = hits + 1;
				core.log("REG-WALK HIT env-name '" .. ENV_NAMES[i]
					.. "' (" .. type(rawget(t, ENV_NAMES[i])) .. ") in "
					.. path);
			end;
		end;
		for i = 1, #METHOD_NAMES do
			if rawget(t, METHOD_NAMES[i]) ~= nil then
				hits = hits + 1;
				core.log("REG-WALK HIT method '" .. METHOD_NAMES[i]
					.. "' (" .. type(rawget(t, METHOD_NAMES[i])) .. ") in "
					.. path);
			end;
		end;
	end;

	local walk;
	walk = function(v, path, depth)
		if nodes >= NODE_BUDGET or depth > MAX_DEPTH then
			return;
		end;
		local vt = type(v);
		if vt == "table" then
			if visited[v] then
				return;
			end;
			visited[v] = true;
			nodes = nodes + 1;
			check_table(v, path);
			local mt = dbg.getmetatable and dbg.getmetatable(v);
			if mt then
				walk(mt, path .. ".<mt>", depth + 1);
			end;
			for k, child in pairs(v) do
				local ct = type(child);
				if ct == "table" or ct == "userdata"
						or ct == "function" then
					local kt = type(k) == "string" and k
						or ("[" .. tostring(k) .. "]");
					walk(child, path .. "." .. kt, depth + 1);
				end;
			end;
		elseif vt == "userdata" then
			local mt = dbg.getmetatable and dbg.getmetatable(v);
			if mt and not visited[mt] then
				walk(mt, path .. ".<udmt>", depth + 1);
			end;
		elseif vt == "function" then
			if visited[v] then
				return;
			end;
			visited[v] = true;
			nodes = nodes + 1;
			-- an engine-attached chunk's environment IS the privileged
			-- env; any function in the registry might carry it
			local ok, env = pcall(function()
				return dbg.getfenv and dbg.getfenv(v) or getfenv(v);
			end);
			if ok and type(env) == "table" and not visited[env] then
				walk(env, path .. ".<fenv>", depth + 1);
			end;
		end;
	end;

	walk(reg, "reg", 0);
	core.log("REG-WALK done: " .. nodes .. " nodes, " .. hits .. " hits"
		.. (nodes >= NODE_BUDGET and " (BUDGET EXHAUSTED)" or ""));
end;

return M;
