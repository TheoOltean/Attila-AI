-------------------------------------------------------------------------
--	ATTILA-AI bootstrap-VM probe (branch custom-battles). The bootstrap
--	battle world loads for EVERY battle -- menu custom battles included
--	(verified 2026-07-08) -- but only engine-ATTACHED chunks get the
--	battle interface environment. This module censuses what a bootstrap
--	VM DOES have, and tests the native door (package.loadlib) from it.
--	Listed by both the battle and frontend orchestrators; core.world
--	tags the log lines.
--
--	DEFAULT-OFF: self-gates on data/aai_menu_probe.txt.
-------------------------------------------------------------------------

local M = {};

function M.init(core)
	local f = io.open("data/aai_menu_probe.txt", "r");
	if not f then
		return;
	end;
	f:close();

	core.log("MENU-PROBE census of the " .. core.world .. " bootstrap VM");

	-- 1. full sorted globals census (chunked lines; the whole point is
	-- to see what IS here, not guess)
	local keys = {};
	for k in pairs(_G) do
		keys[#keys + 1] = tostring(k);
	end;
	table.sort(keys);
	core.log("MENU-PROBE globals: " .. #keys .. " keys");
	local line = {};
	for i = 1, #keys do
		line[#line + 1] = keys[i];
		if #line == 20 or i == #keys then
			core.log("MENU-PROBE   " .. table.concat(line, " "));
			line = {};
		end;
	end;

	-- 2. the battle interface names: reachable as raw globals?
	local names = { "empire_battle", "battle_vector", "battle_manager",
		"get_bm", "tick_increment_counter", "events", "get_events",
		"system", "UIComponent", "battle_setup_info" };
	for i = 1, #names do
		local v = rawget(_G, names[i]);
		core.log("MENU-PROBE " .. names[i] .. " = "
			.. (v ~= nil and type(v) or "nil"));
	end;

	-- 3. debug library (registry access would let us hunt hidden
	-- interface tables from plain Lua)
	local dbg = rawget(_G, "debug");
	core.log("MENU-PROBE debug lib = " .. type(dbg)
		.. (type(dbg) == "table"
			and (" getregistry=" .. type(dbg.getregistry)) or ""));

	-- 4. the native door: package.loadlib proof-of-life. If this works
	-- in a menu custom battle, the DLL runs in-process there and the
	-- engine's own script-attach machinery is callable from native code.
	core.log("MENU-PROBE package.loadlib = " .. type(package.loadlib));
	local lib_ok, opener = pcall(package.loadlib, "data\\aai_native.dll",
		"luaopen_aai");
	if lib_ok and opener then
		local open_ok, open_err = pcall(opener);
		core.log("MENU-PROBE native DLL: loaded, luaopen_aai ok="
			.. tostring(open_ok)
			.. (open_ok and "" or (" -- " .. tostring(open_err))));
	else
		core.log("MENU-PROBE native DLL load FAILED: " .. tostring(opener));
	end;
end;

return M;
