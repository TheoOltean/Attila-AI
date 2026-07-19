-------------------------------------------------------------------------
--	Differential mapper driver (2026-07-14): the in-process "gold hunt".
--	Every 500ms, enumerates EVERY unit on both sides, logs the oracle series
--	(routing/moving/melee/fire/men/ammo...) to data/aai_diff_oracle.log, and
--	calls the DLL aai_snapshot(unit,key,tick) which delta-logs changed struct
--	dwords to data/aai_diff_struct.log. The offline correlator
--	(tools/aai_diff_correlate.py) joins them and labels offsets by the event
--	their changes track. Only surfaces LIVE fields.
-------------------------------------------------------------------------

local M = {};
local DLL = "data\\aai_native.dll";
local ORACLE = "data/aai_diff_oracle.log";
local EVENT  = "data/aai_diff_event.txt";   -- optional manual marker

M.snap = nil; M.reset = nil;

local keymap = {}; local nextkey = 0;
local function keyfor(id)
	if keymap[id] == nil then keymap[id] = nextkey; nextkey = nextkey + 1; end;
	return keymap[id];
end;

local function read(o, m, ...)
	local a = { ... };
	local ok, v = pcall(function() return o[m](o, unpack(a)); end);
	if not ok then return nil; end;
	if type(v) == "function" then local ok2, v2 = pcall(v); return ok2 and v2 or nil; end;
	return v;
end;

-- bitmask of free boolean oracles (Lua-observable states)
local BITS = {
	{1,   "is_routing"}, {2, "is_shattered"}, {4, "is_moving"}, {8, "is_moving_fast"},
	{16,  "is_in_melee"}, {32, "is_idle"}, {64, "is_under_missile_attack"},
	{256, "is_cavalry"}, {512, "is_artillery"}, {1024, "is_infantry"},
};
local function oracle_bits(unit)
	local b = 0;
	for _, e in ipairs(BITS) do if read(unit, e[2]) then b = b + e[1]; end; end;
	if read(unit, "is_behaviour_active", "fire_at_will") then b = b + 128; end;
	return b;
end;

function M.init(core)
	if type(package) ~= "table" or type(package.loadlib) ~= "function" then return; end;
	M.snap  = package.loadlib(DLL, "aai_snapshot");
	M.reset = package.loadlib(DLL, "aai_snapshot_reset");
	if type(M.snap) ~= "function" then core.log("native_diff: aai_snapshot resolve FAILED"); return; end;
	local bm = rawget(_G, "aai_bm");
	if not (bm and bm.battle) then core.log("native_diff: no battle bridge -- inactive"); return; end;
	local battle = bm.battle;
	if type(M.reset) == "function" then pcall(M.reset); end;
	pcall(os.remove, ORACLE);
	core.log("native_diff: differential mapper ARMED (500ms, both alliances)");

	local tick = 0;
	local PUMP = "aai_native_diff_pump";
	_G[PUMP] = core.guarded("native_diff pump", function()
		tick = tick + 1;
		local ev = "";
		do local fh = io.open(EVENT, "r"); if fh then ev = (fh:read("*l") or ""); fh:close(); end; end;
		local out = io.open(ORACLE, "a");
		local alist = read(battle, "alliances");
		local ac = read(alist, "count"); if type(ac) ~= "number" then ac = 0; end;
		for a = 1, ac do
			local armies = read(read(alist, "item", a), "armies");
			local mc = read(armies, "count"); if type(mc) ~= "number" then mc = 0; end;
			for m = 1, mc do
				local units = read(read(armies, "item", m), "units");
				local uc = read(units, "count"); if type(uc) ~= "number" then uc = 0; end;
				for u = 1, uc do
					local unit = read(units, "item", u);
					if unit then
						local id = a .. ":" .. m .. ":" .. tostring(read(unit, "name") or u);
						local key = keyfor(id);
						if key < 160 then
							local men  = read(unit, "number_of_men_alive") or -1;
							local ammo = read(unit, "ammo_left") or -1;
							local ob   = oracle_bits(unit);
							if out then
								out:write(string.format("%d %d %d %d %d %s\n", tick, key, ob, men, ammo, ev == "" and "-" or ev));
							end;
							pcall(M.snap, unit, key, tick);
						end;
					end;
				end;
			end;
		end;
		if out then out:close(); end;
		if tick % 20 == 0 then core.log("native_diff: tick " .. tick .. " units=" .. nextkey); end;
	end);
	local ok = pcall(function() battle:register_repeating_timer(PUMP, 500); end);
	core.log("native_diff: timer reg ok=" .. tostring(ok));
end;

return M;
