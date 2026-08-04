-------------------------------------------------------------------------
--	ATTILA-AI attached chunk (branch custom-battles, Door B Route A).
--	This is NOT a module: the ENGINE loads and runs this file, by name, into
--	a privileged battle script environment, once the attach gate at ctor
--	0x102ca25e sees a non-empty path at BATTLE+0x64128.
--	Engine path: "data/aai/aai_attach.lua" (pack entry aai\aai_attach.lua).
--
--	FIRST RUN = OBSERVE ONLY. Deliberately does not touch the engine:
--	  * NO global named `ClearEventCallbacks` -- the script-interface
--	    destructor looks that name up and CALLS it mid-teardown.
--	  * NO engine timers -- our own register_repeating_timer once killed the
--	    vanilla timer dispatch outright (SCRIPTING.md).
--	  * no bm/battle acquisition, no callbacks, no writes.
--	The whole point is to answer empirically what this environment actually
--	contains, which no amount of further static RE can tell us.
-------------------------------------------------------------------------

local LOG = "data/aai_attach_chunk.txt";

local function w(line)
	local f = io.open(LOG, "a");
	if f then
		f:write(tostring(line) .. "\n");
		f:close();
	end;
end;

w("");
w("==== aai_attach.lua RAN -- THE DOOR IS OPEN ====");
pcall(function() w("time: " .. os.date("%Y-%m-%d %H:%M:%S")); end);

-- 1. what globals does this environment have?
local keys = {};
for k in pairs(_G) do
	keys[#keys + 1] = tostring(k);
end;
table.sort(keys);
w("globals: " .. #keys .. " keys");
local line = {};
for i = 1, #keys do
	line[#line + 1] = keys[i];
	if #line == 12 or i == #keys then
		w("  " .. table.concat(line, " "));
		line = {};
	end;
end;

-- 2. the names that matter: is this the real battle interface?
local want = { "empire_battle", "battle_vector", "battle_manager", "get_bm",
	"tick_increment_counter", "bm", "battle", "events", "UIComponent",
	"conditions", "effect", "package", "require", "loadfile" };
for i = 1, #want do
	local v = rawget(_G, want[i]);
	w("  name " .. want[i] .. " = " .. (v ~= nil and type(v) or "nil"));
end;

-- 3. the __index fallback chain (the sandbox's link to the parent _G)
local mt = getmetatable(_G);
w("getmetatable(_G) = " .. type(mt));
if type(mt) == "table" then
	local idx = rawget(mt, "__index");
	w("  __index = " .. type(idx));
	if type(idx) == "table" then
		local n = 0;
		for _ in pairs(idx) do
			n = n + 1;
		end;
		w("  __index table holds " .. n .. " keys");
	end;
end;

-- 4. if empire_battle exists, what does it expose? (read-only reflection)
local eb = rawget(_G, "empire_battle");
if eb ~= nil then
	w("empire_battle type = " .. type(eb));
	local ebmt = getmetatable(eb);
	w("  metatable = " .. type(ebmt));
	if type(ebmt) == "table" then
		local names = {};
		for k in pairs(ebmt) do
			names[#names + 1] = tostring(k);
		end;
		table.sort(names);
		w("  mt keys: " .. table.concat(names, " "));
	end;
end;

w("==== end aai_attach.lua ====");
