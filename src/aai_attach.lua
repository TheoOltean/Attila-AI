-------------------------------------------------------------------------
--	ATTILA-AI attached chunk (branch custom-battles, Door B Route A).
--	NOT a module: the ENGINE loads and runs this file by name into the
--	script-interface env, once the attach gate sees a non-empty path at
--	BATTLE+0x64128. Engine path: "data/aai/aai_attach.lua".
--
--	RUN 1 (2026-08-04) proved the attach works but landed us in an
--	UNPRIVILEGED env: the same 49 globals as the bootstrap world, and
--	getmetatable(_G) == nil. This version answers WHY, and whether the
--	battle API shows up later.
--
--	Still observe-only. Deliberately:
--	  * NO global named `ClearEventCallbacks` (the interface destructor
--	    looks that name up and CALLS it during teardown).
--	  * NO engine timers (ours once killed the vanilla timer dispatch).
--	  * no writes to engine state.
-------------------------------------------------------------------------

local LOG = "data/aai_attach_chunk.txt";

local function w(line)
	local f = io.open(LOG, "a");
	if f then
		f:write(tostring(line) .. "\n");
		f:close();
	end;
end;

-- never let this chunk raise: an error here aborts the engine's load
local function try(tag, fn)
	local ok, err = pcall(fn);
	if not ok then
		w("  ERROR in " .. tag .. ": " .. tostring(err));
	end;
end;

local BATTLE_NAMES = { "empire_battle", "battle_vector", "battle_manager",
	"get_bm", "bm", "battle", "tick_increment_counter", "UIComponent",
	"conditions", "effect" };

local function api_present()
	local hits = {};
	for i = 1, #BATTLE_NAMES do
		if rawget(_G, BATTLE_NAMES[i]) ~= nil then
			hits[#hits + 1] = BATTLE_NAMES[i];
		end;
	end;
	return hits;
end;

local function count_globals()
	local n = 0;
	for _ in pairs(_G) do
		n = n + 1;
	end;
	return n;
end;

local function snapshot(tag)
	local hits = api_present();
	w("  [" .. tag .. "] _G=" .. tostring(_G) .. " raw_keys=" .. count_globals()
		.. " mt=" .. type(getmetatable(_G))
		.. " battle_api=" .. (#hits > 0 and table.concat(hits, ",") or "NONE"));
end;

w("");
w("==== aai_attach.lua RUN 2 ====");
try("time", function() w("time: " .. os.date("%Y-%m-%d %H:%M:%S")); end);

-- 1. WHICH globals table are we on? The bootstrap module stamps its own
-- tostring(_G) and a marker into data/aai_attach_install.txt. If the marker
-- is visible here, we are sharing the BOOTSTRAP globals table (a lua_newthread
-- shares globals in 5.1 unless the engine installs a private env), which is
-- the direct explanation for the missing sandbox + missing API.
snapshot("load");
w("  bootstrap marker visible = "
	.. tostring(rawget(_G, "aai_bootstrap_marker")));

-- 2. the Lua registry: does the interface env register anything we can reach?
try("registry", function()
	local dbg = rawget(_G, "debug");
	if type(dbg) ~= "table" or type(dbg.getregistry) ~= "function" then
		w("  registry: debug.getregistry unavailable");
		return;
	end;
	local reg = dbg.getregistry();
	local nk, skeys = 0, {};
	for k in pairs(reg) do
		nk = nk + 1;
		if type(k) == "string" then
			skeys[#skeys + 1] = k;
		end;
	end;
	table.sort(skeys);
	w("  registry: " .. nk .. " entries; string keys: "
		.. table.concat(skeys, " "));
	local loaded = reg._LOADED;
	if type(loaded) == "table" then
		local names = {};
		for k in pairs(loaded) do
			names[#names + 1] = tostring(k);
		end;
		table.sort(names);
		w("  registry._LOADED: " .. table.concat(names, " "));
	end;
end);

-- 3. deep-dump the engine globals that might BE the door: what is in them?
local function dump_table(name, depth)
	local t = rawget(_G, name);
	w("  " .. name .. " = " .. type(t));
	if type(t) ~= "table" then
		return;
	end;
	local keys = {};
	for k in pairs(t) do
		keys[#keys + 1] = tostring(k);
	end;
	table.sort(keys);
	w("    " .. #keys .. " keys");
	local line = {};
	for i = 1, #keys do
		line[#line + 1] = keys[i] .. "(" .. type(t[keys[i]]) .. ")";
		if #line == 6 or i == #keys then
			w("      " .. table.concat(line, " "));
			line = {};
		end;
		if i >= 60 then
			w("      ...cut");
			break;
		end;
	end;
	local mt = getmetatable(t);
	if mt and depth and depth > 0 then
		w("    metatable = table");
	end;
end;

for _, n in ipairs({ "events", "system", "package", "lookup", "udata_lookup",
	"vfs", "defined" }) do
	try("dump " .. n, function() dump_table(n, 1); end);
end;
try("scalars", function()
	w("  CliExecute=" .. type(rawget(_G, "CliExecute"))
		.. " RequireRegister=" .. type(rawget(_G, "RequireRegister"))
		.. " decoda_name=" .. tostring(rawget(_G, "decoda_name")));
end);

-- 4. the native door from THIS env (proves we can do native work from the
-- attached chunk, which is how any future env-fixing would be driven).
try("native", function()
	local ok, fn = pcall(package.loadlib, "data\\aai_native.dll", "luaopen_aai");
	w("  package.loadlib -> " .. type(fn));
	if type(fn) == "function" then
		w("  luaopen_aai call ok = " .. tostring(pcall(fn)));
	end;
end);

-- 5. THE TIMING QUESTION: does the battle API appear LATER? The env has an
-- `events` table; register on everything we can and re-snapshot when one
-- fires. Registration shape is unknown, so try the plausible ones and log
-- which worked. Nothing here touches battle state.
try("listen", function()
	local ev = rawget(_G, "events");
	if type(ev) ~= "table" then
		w("  listen: no events table");
		return;
	end;
	local fired = {};
	local registered, attempted = 0, 0;
	for name, slot in pairs(ev) do
		if attempted >= 24 then
			break;
		end;
		attempted = attempted + 1;
		local cb = function(...)
			if not fired[name] then
				fired[name] = true;
				w("  EVENT FIRED: " .. tostring(name));
				snapshot("event:" .. tostring(name));
			end;
		end;
		-- shape A: events.X is a list of callbacks
		if type(slot) == "table" then
			local ok = pcall(function() table.insert(slot, cb); end);
			if ok then
				registered = registered + 1;
			end;
		-- shape B: events.X is a function that registers a listener
		elseif type(slot) == "function" then
			local ok = pcall(slot, cb);
			if ok then
				registered = registered + 1;
			end;
		end;
	end;
	w("  listen: attempted " .. attempted .. ", registered " .. registered);
end);

w("==== end aai_attach.lua RUN 2 ====");
