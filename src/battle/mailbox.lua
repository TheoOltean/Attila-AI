-------------------------------------------------------------------------
--	Command mailbox: the AI->game half of the external-process loop.
--	An external AI process (tools/ai_stub.py) reads the battle state
--	snapshot that telemetry.lua streams out, and writes orders to
--	data/aai_cmd.txt (atomically, via replace). This module polls that
--	file from the battle timer loop and executes fresh orders through
--	the battle_entry bridge (context law: no engine calls outside the
--	timer).
--
--	Command file format (one order per line, seq guards torn/stale reads):
--	  SEQ <n>
--	  RALLY <x> <z> <run01>     -- all squads, grid formation around point
--	  MOVE <i> <x> <z> <run01>  -- single squad by index
--	  HALT <i>
--	  END <n>
-------------------------------------------------------------------------

local M = {};

local CMD_PATH = "data/aai_cmd.txt";
local TICK_MS = 500;
local GRID_SPACING = 22;	-- metres between units in a rally grid

local api = nil;
local squads = nil;
local last_seq = -1;

local function parse(text)
	local seq = text:match("^SEQ (%d+)");
	local tail = text:match("END (%d+)%s*$");
	if not seq or seq ~= tail then
		return nil;	-- torn write or wrong format
	end;
	local orders = {};
	for line in text:gmatch("[^\r\n]+") do
		local x, z, run = line:match("^RALLY ([%-%d%.]+) ([%-%d%.]+) (%d)");
		if x then
			orders[#orders + 1] = { op = "rally", x = tonumber(x), z = tonumber(z), run = run == "1" };
		else
			local i, mx, mz, mrun = line:match("^MOVE (%d+) ([%-%d%.]+) ([%-%d%.]+) (%d)");
			if i then
				orders[#orders + 1] = { op = "move", i = tonumber(i),
					x = tonumber(mx), z = tonumber(mz), run = mrun == "1" };
			else
				local hi = line:match("^HALT (%d+)");
				if hi then
					orders[#orders + 1] = { op = "halt", i = tonumber(hi) };
				end;
			end;
		end;
	end;
	return tonumber(seq), orders;
end;

local function apply(core, orders)
	local sent, failed = 0, 0;
	for k = 1, #orders do
		local o = orders[k];
		local ok = true;
		if o.op == "rally" then
			-- spread squads on a grid centred on the rally point
			local side = math.ceil(math.sqrt(#squads));
			for i = 1, #squads do
				local row = math.floor((i - 1) / side) - (side - 1) / 2;
				local col = ((i - 1) % side) - (side - 1) / 2;
				local ok1 = pcall(api.move, squads[i].uc,
					o.x + col * GRID_SPACING, o.z + row * GRID_SPACING, o.run);
				if ok1 then sent = sent + 1; else failed = failed + 1; end;
			end;
		elseif o.op == "move" and squads[o.i] then
			ok = pcall(api.move, squads[o.i].uc, o.x, o.z, o.run);
			if ok then sent = sent + 1; else failed = failed + 1; end;
		elseif o.op == "halt" and squads[o.i] then
			ok = pcall(api.halt, squads[o.i].uc);
			if ok then sent = sent + 1; else failed = failed + 1; end;
		end;
	end;
	core.log("mailbox: applied " .. #orders .. " orders (moves sent=" ..
		sent .. " failed=" .. failed .. ")");
end;

function M.init(core)
	local bm = rawget(_G, "aai_bm");
	api = rawget(_G, "aai_api");
	squads = rawget(_G, "aai_enemy_squads");
	if not bm or not api or type(squads) ~= "table" or #squads == 0 then
		core.log("mailbox: no bridge/squads in this instance");
		return;
	end;

	local finished = false;
	local ev = _G.events;
	if type(ev) == "table" and ev.BattleCompleted then
		ev.BattleCompleted[#ev.BattleCompleted + 1] =
			function() finished = true; end;	-- flag only (context law)
	end;

	local function tick()
		if finished then
			core.log("mailbox: battle complete - stopped");
			return;
		end;
		local f = io.open(CMD_PATH, "r");
		if f then
			local text = f:read("*a");
			f:close();
			local seq, orders = parse(text or "");
			if seq and seq ~= last_seq then
				last_seq = seq;
				core.log("mailbox: seq " .. seq .. " (" .. #orders .. " orders)");
				local ok, err = pcall(apply, core, orders);
				if not ok then
					core.log("mailbox: apply FAILED: " .. tostring(err));
				end;
			end;
		end;
		bm:callback(core.guarded("mailbox tick", tick), TICK_MS, "aai_mailbox");
	end;

	core.log("mailbox: polling " .. CMD_PATH .. " every " .. TICK_MS .. "ms (" ..
		#squads .. " squads under external control)");
	tick();
end;

return M;
