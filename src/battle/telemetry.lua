-------------------------------------------------------------------------
--	Battle telemetry: rewrites data/attila_ai_battle_state.txt with a
--	fixed-layout snapshot (camera + every unit in the player's army)
--	several times a second. Watch it with tools/watch_battle.sh, which
--	repaints in place instead of scrolling.
--
--	The battle interface is handed over by battle_entry.lua as _G.aai_bm
--	(a vanilla battle_manager) — see battle_entry.lua for why it cannot
--	be read directly from here. Ticking rides the battle manager's own
--	callback system. Campaign battles only.
-------------------------------------------------------------------------

local M = {};

local SNAPSHOT_PATH = "data/attila_ai_battle_state.txt";
local TICK_MS = 250;
local TIMER_NAME = "aai_battle_telemetry_tick";	-- fallback engine timer

local bm = nil;		-- vanilla battle_manager (preferred route)
local battle = nil;	-- raw engine battle interface
local phase = "loading";
local ticks = 0;

local RETRY_EVENTS = {
	"BattleDeploymentPhaseCommenced",
	"BattleConflictPhaseCommenced",
};

-- pcall a method on obj; nil if missing/failing
local function read(obj, method, ...)
	local args = { ... };
	local ok, value = pcall(function() return obj[method](obj, unpack(args)); end);
	if not ok then
		return nil;
	end;
	if type(value) == "function" then
		-- bound getter instead of value (Attila interface quirk)
		local ok2, value2 = pcall(value);
		if ok2 then
			return value2;
		end;
		return nil;
	end;
	return value;
end;

local function fmt_num(value, width, decimals)
	if type(value) ~= "number" then
		return string.rep(" ", width - 1) .. "?";
	end;
	return string.format("%" .. width .. "." .. decimals .. "f", value);
end;

local function vector_text(v)
	if not v then
		return "x=       ? y=       ? z=       ?";
	end;
	return string.format("x=%s y=%s z=%s",
		fmt_num(read(v, "get_x"), 8, 1),
		fmt_num(read(v, "get_y"), 8, 1),
		fmt_num(read(v, "get_z"), 8, 1));
end;

local function unit_line(index, unit)
	local men     = read(unit, "number_of_men_alive");
	local men0    = read(unit, "initial_number_of_men");
	local ammo    = read(unit, "ammo_left");
	local pos     = read(unit, "position");
	local flags   = "";
	if read(unit, "is_shattered") then flags = flags .. "SHAT ";
	elseif read(unit, "is_routing") then flags = flags .. "ROUT "; end;
	if read(unit, "is_cavalry") then flags = flags .. "CAV "; end;

	return string.format("%2d  %-32s %s brg=%s men=%s/%s ammo=%s  %s",
		index,
		tostring(read(unit, "name") or "?"),
		vector_text(pos),
		fmt_num(read(unit, "bearing"), 6, 1),
		fmt_num(men, 3, 0), fmt_num(men0, 3, 0),
		fmt_num(ammo, 3, 0),
		flags);
end;

local function player_army()
	local alliances = read(battle, "alliances");
	if not alliances then
		return nil, "?", "?";
	end;
	local a = read(battle, "local_alliance") or 1;
	local b = read(battle, "local_army") or 1;
	local alliance = read(alliances, "item", a);
	if not alliance then
		return nil, a, b;
	end;
	local armies = read(alliance, "armies");
	local army = armies and read(armies, "item", b);
	return army, a, b;
end;

local function snapshot()
	local lines = {};
	lines[#lines + 1] = string.format(
		"==== ATTILA-AI battle  t=%7.1fs  phase=%-10s ====",
		ticks * TICK_MS / 1000, phase);

	local cam = read(battle, "camera");
	lines[#lines + 1] = "CAMERA  pos " .. vector_text(cam and read(cam, "position"));
	lines[#lines + 1] = "        tgt " .. vector_text(cam and read(cam, "target"));

	local army, a, b = player_army();
	if not army then
		lines[#lines + 1] = "player army not found (alliance=" ..
			tostring(a) .. " army=" .. tostring(b) .. ")";
	else
		local units = read(army, "units");
		local count = (units and read(units, "count")) or 0;
		lines[#lines + 1] = string.format(
			"PLAYER ARMY  alliance=%s army=%s  units=%d", tostring(a), tostring(b), count);
		for i = 1, count do
			local unit = read(units, "item", i);
			lines[#lines + 1] = unit and unit_line(i, unit) or (i .. "  <unreadable>");
		end;
	end;

	local f = io.open(SNAPSHOT_PATH, "w");
	if f then
		f:write(table.concat(lines, "\n") .. "\n");
		f:close();
	end;
end;

function M.init(core)
	local started = false;

	local bless = rawget(_G, "aai_bless");
	if bless then
		bless(read);
	end;

	-- self-rescheduling tick on the battle manager's callback system;
	-- stops itself once the battle completes
	local function tick()
		if phase == "complete" then
			return;
		end;
		ticks = ticks + 1;
		snapshot();
		bm:callback(core.guarded("telemetry tick", tick), TICK_MS, "aai_telemetry_tick");
	end;

	local function try_start(where)
		if started then
			return;
		end;

		local mgr = rawget(_G, "aai_bm");
		if mgr and mgr.battle then
			bm = mgr;
			battle = mgr.battle;
		elseif type(_G.battle) == "userdata" or type(_G.battle) == "table" then
			battle = _G.battle;
		end;
		if not battle then
			core.log("telemetry: no interface at " .. where);
			return;
		end;

		if bm then
			local ok, err = pcall(function()
				bm:callback(core.guarded("telemetry tick", tick), TICK_MS, "aai_telemetry_tick");
			end);
			if ok then
				started = true;
				core.log("telemetry: ticking via battle_manager every " ..
					TICK_MS .. "ms -> " .. SNAPSHOT_PATH);
			else
				core.log("telemetry: bm:callback FAILED: " .. tostring(err));
				bm = nil;
				battle = nil;
			end;
		else
			-- raw interface without a manager: engine repeating timer,
			-- which calls the global function named TIMER_NAME
			_G[TIMER_NAME] = core.guarded("telemetry tick", function()
				ticks = ticks + 1;
				snapshot();
			end);
			local ok = pcall(function() battle:register_repeating_timer(TIMER_NAME, TICK_MS); end);
			if ok then
				started = true;
				core.log("telemetry: engine timer every " .. TICK_MS .. "ms -> " .. SNAPSHOT_PATH);
			else
				core.log("telemetry: register_repeating_timer FAILED");
				battle = nil;
			end;
		end;
	end;

	try_start("load");

	-- the bootstrap instance of this module has no interface — that is
	-- expected; the entry-script instance is the one that starts
	local ev = _G.events;
	if type(ev) ~= "table" then
		return;
	end;

	for i = 1, #RETRY_EVENTS do
		local name = RETRY_EVENTS[i];
		if ev[name] then
			ev[name][#ev[name] + 1] = core.guarded("telemetry " .. name, function()
				phase = (name == "BattleDeploymentPhaseCommenced") and "deployment" or "conflict";
				try_start(name);
			end);
		end;
	end;

	if ev.BattleCompleted then
		ev.BattleCompleted[#ev.BattleCompleted + 1] =
			core.guarded("telemetry BattleCompleted", function()
				-- flag only: engine calls are broken in event context
				phase = "complete";	-- tick sees this and stops rescheduling
			end);
	end;
end;

return M;
