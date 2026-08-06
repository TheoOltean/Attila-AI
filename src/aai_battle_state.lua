-------------------------------------------------------------------------
--	ATTILA-AI orchestrator for the per-battle script state (entered via
--	battle_entry.lua / the custom-battlefield hook). empire_battle is
--	available here, so modules get the full battle interface.
--
--	To add a feature for this state: create src/battle/<name>.lua
--	(returning a table with init(core)) and list it below.
-------------------------------------------------------------------------

-- DISPATCH BISECT (2026-08-06): the bridge ticks; loading THIS file kills the
-- timer dispatch even with every module skipped. data/aai_stack_stop.txt holds
-- a stop point so the remaining three statements can be split one battle at a
-- time: 0 = return before `require "aai_core"` (the only real require left in
-- the chain -- everything else is stdio+loadstring), 1 = after the require,
-- 2 = after the log header. No file = normal bring-up.
local STOP = nil;
do
	local fh = io.open("data/aai_stack_stop.txt", "r");
	if fh then
		STOP = tonumber(string.match(fh:read("*a") or "", "%d+"));
		fh:close();
	end;
end;
local function halt(point)
	if STOP ~= nil and STOP <= point then
		local f = io.open("data/aai_attach_chunk.txt", "a");
		if f then
			f:write("  [stack] STOP " .. tostring(STOP) .. " -- halted at point "
				.. point .. " (aai_stack_stop.txt)\n");
			f:close();
		end;
		return true;
	end;
	return false;
end;

if halt(0) then
	return;
end;

-- FIX ATTEMPT (2026-08-06): `require` is the last call in this chain that
-- reaches the ENGINE's own loader -- every other module is pre-stuffed into
-- package.loaded by the kernel and hits the cache. Every battle tonight that
-- ticked ran no real require; all three that ran this line died. The kernel
-- already loads eight chunks with plain stdio + loadstring in ticking
-- battles, so aai_core loads the same way, and package.loaded is seeded so
-- nothing downstream reaches the loader either.
local core;
do
	local cached = package.loaded["aai_core"];
	if type(cached) == "table" then
		core = cached;
	else
		local fh = io.open("data/aai_dev/aai_core.lua", "r");
		if fh then
			local src = fh:read("*a");
			fh:close();
			local chunk = loadstring(src, "@data/aai_dev/aai_core.lua");
			if chunk then
				local ok, mod = pcall(chunk);
				if ok and type(mod) == "table" then
					core = mod;
					package.loaded["aai_core"] = mod;
				end;
			end;
		end;
		if core == nil then
			core = require "aai_core";	-- no dev copy: pack, via the loader
		end;
	end;
end;
core.world = "battle+";

if halt(1) then
	return;
end;

core.log_header("battle script state loaded (custom battlefield hook)");

if halt(2) then
	return;
end;

-- DISPATCH BISECT (2026-08-06): kernel+timer alone ticks 4800+; adding this
-- payload kills the timer dispatch before tick 1. Each module can be skipped
-- with data/aai_skip_<last path element>.txt (e.g. aai_skip_probe.txt) so the
-- guilty init is found one battle at a time. This file is dev-shadowed --
-- py tools/install_loose.py delivers it, NO pack rebuild. Levers absent =
-- normal bring-up, so this is inert in campaign and in normal play.
local function wanted(list)
	local keep = {};
	for i = 1, #list do
		local name = list[i];
		local slug = string.match(name, "([^/]+)$") or name;
		local fh = io.open("data/aai_skip_" .. slug .. ".txt", "r");
		if fh then
			fh:close();
			core.log("module SKIPPED by lever: " .. name);
		else
			keep[#keep + 1] = name;
		end;
	end;
	return keep;
end;

core.load_modules(wanted({
	"battle/api",      -- unified battle API: read_state() + issue() (providers lua+native+db)
	"battle/publish",  -- READ driver: full snapshot -> data/aai_battle.json every 500ms
	-- "battle/control" RETIRED 2026-07-29 with the cockpit AI-control UI (Theo:
	--   "of no use to me anymore") -- the harness is the single write path now.
	"battle/probe",    -- T1 capability probe harness. DEFAULT-OFF: self-gates on
	--   data/aai_probe_on.txt (create to arm, delete to disarm). Exercises every
	--   unverified T1 read/write on one enemy guinea-pig unit and logs verdicts
	--   (grep log for "PROBE"); results also in data/aai_probe.json.
	"battle/harness",  -- interactive capability tester (the cockpit /harness page).
	--   DEFAULT-OFF: self-gates on data/aai_harness_on.txt. Applies one test
	--   order at a time from data/aai_test_order.txt, acks to aai_test_ack.json.
	-- "battle/native_diff",  -- OPT-IN research tool (differential offset mapper).
	--   Uncomment for a mapping session; off by default (perf + log volume).
}));
