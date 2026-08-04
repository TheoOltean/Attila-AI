-------------------------------------------------------------------------
--	ATTILA-AI attached chunk (branch custom-battles, Door B Route A).
--	NOT a module: the ENGINE loads and runs this by name into the script
--	interface, once the attach gate sees a non-empty BATTLE+0x64128.
--
--	WHERE WE ARE (runs 1-3):
--	  * The attach works: the engine loads and runs this file in a
--	    player-configured CUSTOM battle.
--	  * We execute on the BOOTSTRAP globals table (no sandbox was installed
--	    for us), so the battle API is not in _G.
--	  * BUT run 3 FOUND THE PRIVILEGED TABLE anyway: registry -> thread
--	    reg[2] -> getfenv of a function on its stack -> a 27-key table
--	    holding empire_battle, battle.unit(s), battle.army/armies,
--	    battle.alliance(s), battle.unit_controller, battle.camera,
--	    battle_vector, UIComponent, ScriptedValueRegistry, out.
--	  * Every one of those carries a `new` in its __index, and the method
--	    surface is real: empire_battle has 77 methods, battle.unit 45,
--	    battle.unit_controller 67.
--	  * Events fire and register fine (262/264), contexts are userdata.
--
--	RUN 4: actually USE it. Instantiate empire_battle and walk
--	alliances -> armies -> units, logging real unit data. If that prints
--	live unit names and positions, we have the battle API in a custom
--	battle and the branch's goal is met.
--
--	READ-ONLY BY CHOICE: only query methods are called here. No
--	unit_controller actions, no timers, no ClearEventCallbacks global,
--	no writes. Control comes later, deliberately.
-------------------------------------------------------------------------

local LOG = "data/aai_attach_chunk.txt";
local BUDGET = 1500;

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

w("");
w("==== aai_attach.lua RUN 4 -- USE THE API ====");
try("time", function() w("time: " .. os.date("%Y-%m-%d %H:%M:%S")); end);

-- ---- 1. recover the privileged table (run 3's proven route) -----------
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
	-- the registry itself also carries the type tables
	if not P then
		consider(reg);
	end;
	w("  privileged table = " .. tostring(P));
end);

if not P then
	w("  ABORT: could not reach the privileged table this run");
	w("==== end RUN 4 ====");
	return;
end;

-- ---- 2. instantiate empire_battle ------------------------------------
local EB = rawget(P, "empire_battle");
w("  empire_battle = " .. type(EB));

local battle = nil;
try("instantiate", function()
	-- try the plausible constructor shapes; log which one takes
	local forms = {
		{ "empire_battle:new()", function() return EB:new(); end },
		{ "empire_battle.new()", function() return EB.new(); end },
	};
	for i = 1, #forms do
		local ok, res = pcall(forms[i][2]);
		w("  " .. forms[i][1] .. " -> " .. (ok and type(res) or ("ERR " .. tostring(res))));
		if ok and res ~= nil and battle == nil then
			battle = res;
			w("  *** BATTLE INTERFACE ACQUIRED via " .. forms[i][1]
				.. " -> " .. tostring(res));
		end;
	end;
end);

if battle == nil then
	w("  no interface instance; nothing further to try this run");
	w("==== end RUN 4 ====");
	return;
end;

-- ---- 3. walk alliances -> armies -> units (READ ONLY) ----------------
local function call(obj, name, ...)
	local f = nil;
	local ok = pcall(function() f = obj[name]; end);
	if not ok or type(f) ~= "function" then
		return nil, "no method " .. name;
	end;
	local res = { pcall(f, obj, ...) };
	if not res[1] then
		return nil, tostring(res[2]);
	end;
	return res[2];
end;

try("walk", function()
	local alliances, err = call(battle, "alliances");
	w("  battle:alliances() -> " .. (alliances and type(alliances)
		or ("FAILED " .. tostring(err))));
	if not alliances then
		return;
	end;
	local n = call(alliances, "count") or 0;
	w("  alliances:count() = " .. tostring(n));
	for ai = 1, (tonumber(n) or 0) do
		local alliance = call(alliances, "item", ai);
		if alliance then
			local armies = call(alliance, "armies");
			local an = armies and call(armies, "count") or 0;
			w("  alliance " .. ai .. ": armies=" .. tostring(an));
			for bi = 1, (tonumber(an) or 0) do
				local army = call(armies, "item", bi);
				if army then
					local units = call(army, "units");
					local un = units and call(units, "count") or 0;
					w("    army " .. bi .. ": units=" .. tostring(un));
					for ui = 1, math.min(tonumber(un) or 0, 8) do
						local u = call(units, "item", ui);
						if u then
							local nm  = call(u, "name");
							local ty  = call(u, "type");
							local men = call(u, "number_of_men_alive");
							local rt  = call(u, "is_routing");
							local pos = call(u, "position");
							local px, py, pz;
							if pos then
								px = call(pos, "get_x");
								py = call(pos, "get_y");
								pz = call(pos, "get_z");
							end;
							w(string.format(
								"      unit %d: name=%s type=%s men=%s routing=%s pos=(%s,%s,%s)",
								ui, tostring(nm), tostring(ty), tostring(men),
								tostring(rt), tostring(px), tostring(py), tostring(pz)));
						end;
					end;
				end;
			end;
		end;
	end;
end);

w("==== end RUN 4 ====");
