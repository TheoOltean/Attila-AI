-------------------------------------------------------------------------
--	ATTILA-AI attached chunk (branch custom-battles, Door B Route A).
--	NOT a module: the ENGINE loads and runs this by name into the script
--	interface, once the attach gate sees a non-empty BATTLE+0x64128.
--
--	RUN 1: attach works; env was the plain bootstrap surface.
--	RUN 2: proved WHY -- we run on the BOOTSTRAP globals table
--	  (identical tostring(_G), marker visible, decoda_name="Parent State"),
--	  so no private sandbox was installed for us. BUT the registrar DID run:
--	  the shared Lua registry grew 5 -> 32 entries and now holds the battle
--	  API metatables (empire_battle, battle.unit(s), battle.army/armies,
--	  battle.alliance(s), battle.unit_controller, battle.camera, ...).
--	  Registry is shared by every thread of the state, so it is reachable
--	  from right here.
--
--	RUN 3 goals, in order of value:
--	  A. EVENTS DONE RIGHT. Run 2 registered on only 24 arbitrary (hash
--	     order) events and none fired. Register on ALL of them; an event
--	     context carries live battle userdata, which would give us the API
--	     without ever needing the privileged globals.
--	  B. Find the PRIVILEGED globals table: walk every registry value, and
--	     any thread found there, for a table that actually contains
--	     empire_battle as a key.
--	  C. Map the API surface: dump the registry metatables' __index method
--	     tables, so we know what we can call once we hold an instance.
--
--	Still observe-only: no engine writes, no timers, no global named
--	ClearEventCallbacks.
-------------------------------------------------------------------------

local LOG = "data/aai_attach_chunk.txt";
local BUDGET = 1200;			-- hard cap on log lines; events can be chatty

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
end;

local BATTLE_NAMES = { "empire_battle", "battle_vector", "battle_manager",
	"get_bm", "bm", "battle", "tick_increment_counter", "UIComponent" };

local function api_hits(t)
	local hits = {};
	for i = 1, #BATTLE_NAMES do
		local ok, v = pcall(rawget, t, BATTLE_NAMES[i]);
		if ok and v ~= nil then
			hits[#hits + 1] = BATTLE_NAMES[i];
		end;
	end;
	return hits;
end;

local function snapshot(tag)
	local hits = api_hits(_G);
	local n = 0;
	for _ in pairs(_G) do
		n = n + 1;
	end;
	w("  [" .. tag .. "] _G=" .. tostring(_G) .. " keys=" .. n
		.. " api=" .. (#hits > 0 and table.concat(hits, ",") or "NONE"));
end;

w("");
w("==== aai_attach.lua RUN 3 ====");
try("time", function() w("time: " .. os.date("%Y-%m-%d %H:%M:%S")); end);
snapshot("load");

local dbg = rawget(_G, "debug");
local reg = nil;
if type(dbg) == "table" and type(dbg.getregistry) == "function" then
	reg = dbg.getregistry();
end;

-- ---- B. hunt the privileged globals table -----------------------------
-- Any table anywhere in the registry that holds `empire_battle` IS the env
-- the engine built for the interface. Also chase thread objects: a function
-- on a thread's stack carries its environment via getfenv.
try("hunt-env", function()
	if not reg then
		w("  hunt: no registry");
		return;
	end;
	local seen, found = {}, 0;
	local function consider(t, path)
		if type(t) ~= "table" or seen[t] then
			return;
		end;
		seen[t] = true;
		local ok, v = pcall(rawget, t, "empire_battle");
		if ok and v ~= nil then
			found = found + 1;
			w("  *** PRIVILEGED TABLE at " .. path .. " -> " .. tostring(t));
			local names = {};
			for k in pairs(t) do
				names[#names + 1] = tostring(k);
			end;
			table.sort(names);
			w("      " .. #names .. " keys: "
				.. table.concat(names, " "):sub(1, 900));
		end;
	end;
	for k, v in pairs(reg) do
		local key = tostring(k);
		consider(v, "reg[" .. key .. "]");
		if type(v) == "table" then
			for k2, v2 in pairs(v) do
				consider(v2, "reg[" .. key .. "][" .. tostring(k2) .. "]");
			end;
		elseif type(v) == "thread" then
			w("  registry thread at reg[" .. key .. "] -> " .. tostring(v));
			-- functions live on the thread's stack; their fenv is its globals
			for lvl = 0, 12 do
				local ok, info = pcall(dbg.getinfo, v, lvl, "f");
				if not ok or type(info) ~= "table" or info.func == nil then
					break;
				end;
				local ok2, env = pcall(getfenv, info.func);
				if ok2 and type(env) == "table" then
					consider(env, "fenv(reg[" .. key .. "] lvl " .. lvl .. ")");
				end;
			end;
		end;
	end;
	w("  hunt: " .. found .. " privileged table(s) found");
end);

-- ---- C. map the API surface we already hold ---------------------------
try("api-surface", function()
	if not reg then
		return;
	end;
	local want = { "empire_battle", "battle.units", "battle.unit",
		"battle.armies", "battle.army", "battle.alliances",
		"battle.unit_controller", "battle_vector" };
	for i = 1, #want do
		local v = rawget(reg, want[i]);
		w("  reg[\"" .. want[i] .. "\"] = " .. type(v));
		if type(v) == "table" then
			local keys = {};
			for k in pairs(v) do
				keys[#keys + 1] = tostring(k);
			end;
			table.sort(keys);
			w("    keys: " .. table.concat(keys, " "):sub(1, 700));
			local idx = rawget(v, "__index");
			w("    __index = " .. type(idx));
			if type(idx) == "table" then
				local mk = {};
				for k in pairs(idx) do
					mk[#mk + 1] = tostring(k);
				end;
				table.sort(mk);
				w("    METHODS(" .. #mk .. "): "
					.. table.concat(mk, " "):sub(1, 900));
			end;
		end;
	end;
end);

-- ---- A. events: register on EVERYTHING --------------------------------
-- An event context carries live battle userdata. If any fires we log the
-- context's shape and its metatable -- that is the road into the API that
-- does not depend on globals at all.
try("listen-all", function()
	local ev = rawget(_G, "events");
	if type(ev) ~= "table" then
		w("  listen: no events table");
		return;
	end;
	local fired, nreg, ntried = {}, 0, 0;
	for name, slot in pairs(ev) do
		ntried = ntried + 1;
		local ename = tostring(name);
		local cb = function(context)
			if fired[ename] then
				return;
			end;
			fired[ename] = true;
			w("  EVENT " .. ename .. " ctx=" .. type(context));
			snapshot("ev:" .. ename);
			if type(context) == "table" or type(context) == "userdata" then
				local mt = getmetatable(context);
				w("    ctx metatable = " .. type(mt));
				if type(mt) == "table" then
					local mk = {};
					for k in pairs(mt) do
						mk[#mk + 1] = tostring(k);
					end;
					table.sort(mk);
					w("    ctx mt keys: "
						.. table.concat(mk, " "):sub(1, 500));
					local idx = rawget(mt, "__index");
					if type(idx) == "table" then
						local ik = {};
						for k in pairs(idx) do
							ik[#ik + 1] = tostring(k);
						end;
						table.sort(ik);
						w("    ctx METHODS: "
							.. table.concat(ik, " "):sub(1, 700));
					end;
				end;
			end;
		end;
		if type(slot) == "table" then
			if pcall(function() slot[#slot + 1] = cb; end) then
				nreg = nreg + 1;
			end;
		elseif type(slot) == "function" then
			if pcall(slot, cb) then
				nreg = nreg + 1;
			end;
		end;
	end;
	w("  listen: " .. ntried .. " events, registered on " .. nreg);
end);

w("==== end aai_attach.lua RUN 3 (events may append below) ====");
