-------------------------------------------------------------------------
--	ATTILA-AI attached chunk (branch custom-battles, Door B Route A).
--	NOT a module: the ENGINE loads and runs this by name into the script
--	interface, once the attach gate sees a non-empty BATTLE+0x64128.
--
--	RUN 6: KERNEL + RELOADABLE PAYLOAD. Run 5 (verified live) proved the
--	whole stack; this run splits it for the dev loop:
--	  * THIS FILE is the KERNEL -- the smallest possible permanent core:
--	    privileged-table recovery, empire_battle:new(), the phase tracker,
--	    the single engine timer, and the reload supervisor. It ships in the
--	    pack and changes rarely (kernel edits still need a pack rebuild).
--	  * custom_bridge.lua is the PAYLOAD -- the bridge closures + the whole
--	    module stack bring-up. It is re-required from disk on demand, so
--	    logic edits land MID-BATTLE with no rebuild and no restart.
--
--	DEV SHADOW: the kernel prepends a package.loaders searcher that reads
--	data/aai_dev/<module>.lua with plain io.open + loadstring -- real disk,
--	fresh content every load, no VFS assumptions. The cockpit's reload
--	button copies src/ there and bumps data/aai_reload.txt; the kernel
--	tears the payload down (event handlers truncated to a snapshot taken
--	before the first load, hook globals cleared) and re-requires it.
--
--	CRASH BREAKER: attach_install writes data/aai_attach_inflight.txt
--	before arming; reaching the end of this kernel deletes it -- so a
--	marker that survives means the armed battle died in the attach window,
--	and the next battle load disarms itself instead of retrying forever.
-------------------------------------------------------------------------

local LOG = "data/aai_attach_chunk.txt";
local BUDGET = 400;

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

-- re-entry guard: the engine one-shot loads us, but A:reload_battle_script
-- re-runs the file; the kernel must never double its timer or handlers
if rawget(_G, "aai_stack_up") then
	w("==== aai_attach.lua re-entry: kernel already up (use the cockpit "
		.. "reload for the payload) ====");
	return;
end;

-- TIMER-DISPATCH BISECT (2026-08-05): the dispatch serviced run 5's
-- registration (coastal, 10k+ ticks) but services NOTHING on run 6
-- (plains AND coastal, repeating AND singleshot). Lever data/aai_run5.txt
-- replays the VERIFIED run-5 chunk byte-for-byte (src/aai_run5.lua =
-- git b36a7e5) on this install: ticks there => the regression is run-6
-- kernel content; dead there => it is outside the chunk (DLL rebuild /
-- installer io / environment).
local r5 = io.open("data/aai_run5.txt", "r");
if r5 then
	r5:close();
	w("");
	w("==== RUN5-MODE (data/aai_run5.txt): executing aai_run5.lua verbatim ====");
	local fn5, ferr5 = loadfile("data/aai/aai_run5.lua");
	if fn5 then
		local okr, rerr = pcall(fn5);
		if not okr then
			w("  run5-mode ERROR: " .. tostring(rerr));
		end;
	else
		w("  run5-mode loadfile FAILED: " .. tostring(ferr5));
	end;
	return;
end;

w("");
w("==== aai_attach.lua RUN 6 -- KERNEL ====");
try("time", function() w("time: " .. os.date("%Y-%m-%d %H:%M:%S")); end);

-- ---- 1. recover the privileged table (proven route) -------------------
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
	if not P then
		consider(reg);
	end;
	w("  privileged table = " .. tostring(P));
end);

if not P then
	w("  ABORT: could not reach the privileged table this run");
	w("==== end RUN 6 ====");
	return;
end;

-- ---- 2. instantiate the battle interface ------------------------------
local EB = rawget(P, "empire_battle");
local battle = nil;
try("instantiate", function()
	battle = EB:new();		-- colon form required (run 4: dot form -> nil)
end);
w("  empire_battle:new() -> " .. tostring(battle));

if battle == nil then
	w("  ABORT: no interface instance");
	w("==== end RUN 6 ====");
	return;
end;

-- ---- 3. kernel state the payload (re)builds from ----------------------
rawset(_G, "aai_priv", P);
rawset(_G, "aai_raw_battle", battle);
local ok_id, batid = pcall(function() return os.date("%Y%m%d_%H%M%S"); end);
rawset(_G, "aai_battle_id", (ok_id and batid) or "battle");	-- once per battle:
	-- the stale-layer contract keys on this, so a reload must NOT change it
rawset(_G, "aai_tick_count", 0);

-- ---- 4. dev shadow: package.loaded pre-stuff (NO loader mutation) -----
-- Loose repo copies under data/aai_dev/ shadow the pack, read with stdio
-- (always-fresh real disk, no VFS assumptions). Mechanism: execute each
-- dev copy dependency-first and park the result in package.loaded, so the
-- later requires hit the cache and never reach a loader at all.
--
-- HARD RULE (crash 3/3 on 2026-08-05): NEVER touch package.loaders or
-- require. The vanilla bootstrap points package.path at data/ui/ -- the
-- engine's OWN UI requires run through that same package plumbing right
-- as the loading screen ends, and a searcher inserted at loaders[1]
-- killed the game there, before a single timer tick, every time. All
-- writes here are scoped to OUR module names in package.loaded only.
--
-- DEV_ORDER lists PASSIVE modules (top-level = requires + table building,
-- no engine calls, no init) in dependency order. The two orchestrators
-- (aai_battle_state, custom_bridge) run code at top level, so they are
-- loaded directly by the kernel/payload instead (dev_chunk below).
local DEV_ORDER = {
	"aai_json", "battle/db_abilities", "battle/native", "battle/db",
	"battle/api", "battle/publish", "battle/probe", "battle/harness",
};
local FAMILY = { "aai_json", "battle/db_abilities", "battle/native",
	"battle/db", "battle/api", "battle/publish", "battle/probe",
	"battle/harness", "aai_battle_state", "custom_bridge" };

local function dev_path(name)
	return "data/aai_dev/" .. string.gsub(name, "%.", "/") .. ".lua";
end;

-- compiled chunk of a dev copy, or nil if absent/broken (reason logged)
local function dev_chunk(name)
	local fh = io.open(dev_path(name), "r");
	if not fh then
		return nil;
	end;
	local src = fh:read("*a");
	fh:close();
	local chunk, cerr = loadstring(src, "@" .. dev_path(name));
	if not chunk then
		w("  dev copy BROKEN " .. name .. ": " .. tostring(cerr));
		return nil;
	end;
	return chunk;
end;

local function family_clear()
	if type(package) ~= "table" or type(package.loaded) ~= "table" then
		return;
	end;
	for _, name in ipairs(FAMILY) do
		package.loaded[name] = nil;
	end;
end;

local function dev_prestuff()
	local n = 0;
	for _, name in ipairs(DEV_ORDER) do
		local chunk = dev_chunk(name);
		if chunk then
			local ok, mod = pcall(chunk);
			if ok then
				package.loaded[name] = (mod == nil) and true or mod;
				n = n + 1;
			else
				w("  dev copy ERROR " .. name .. ": " .. tostring(mod));
			end;
		end;
	end;
	return n;
end;

-- ---- 5. kernel phase tracker + event snapshot -------------------------
-- The kernel owns phase truth in _G.aai_phase; modules seed their local
-- phase from it at init, so a mid-battle reload does not regress to
-- "loading" (the phase events fired long before the reload).
rawset(_G, "aai_phase", "loading");
local ev_snap = {};
try("phase+snapshot", function()
	local ev = rawget(_G, "events");
	if type(ev) ~= "table" then
		w("  WARNING: no events table -- phase tracking dead");
		return;
	end;
	local function on(name, val)
		ev[name] = ev[name] or {};
		ev[name][#ev[name] + 1] = function()
			rawset(_G, "aai_phase", val);
		end;
	end;
	on("BattleDeploymentPhaseCommenced", "deployment");
	on("BattleConflictPhaseCommenced", "conflict");
	on("BattleCompleted", "complete");
	-- snapshot AFTER kernel handlers, BEFORE any payload handlers: teardown
	-- truncates every handler list back to exactly this state
	for name, t in pairs(ev) do
		if type(t) == "table" then
			ev_snap[name] = #t;
		end;
	end;
	w("  phase tracker up; event snapshot over " .. tostring((function()
		local n = 0;
		for _ in pairs(ev_snap) do n = n + 1; end;
		return n;
	end)()) .. " events");
end);

-- ---- 6. payload load / teardown ---------------------------------------
local PAYLOAD_NAME = "custom_bridge";

local function payload_teardown()
	-- remove every event handler the payload added (kernel's survive)
	pcall(function()
		local ev = rawget(_G, "events");
		if type(ev) ~= "table" then
			return;
		end;
		for name, t in pairs(ev) do
			if type(t) == "table" then
				local keep = ev_snap[name] or 0;
				for i = #t, keep + 1, -1 do
					t[i] = nil;
				end;
			end;
		end;
	end);
	-- unhook the pump riders; module init re-sets them
	rawset(_G, "aai_pub_kick", nil);
	rawset(_G, "aai_harness_tick", nil);
	rawset(_G, "aai_probe_tick", nil);
	pcall(family_clear);
end;

local function payload_load(tag)
	local stuffed = 0;
	pcall(function() stuffed = dev_prestuff(); end);
	local ok, err;
	local chunk = dev_chunk(PAYLOAD_NAME);
	if chunk then
		ok, err = pcall(chunk);
		if ok then
			pcall(function() package.loaded[PAYLOAD_NAME] = true; end);
		end;
	else
		ok, err = pcall(require, PAYLOAD_NAME);
	end;
	w("  payload [" .. tag .. "] dev=" .. stuffed .. " -> "
		.. (ok and "OK" or tostring(err)));
	return ok, err;
end;

local first_ok = payload_load("initial");

-- kernel reached: the attach/bring-up crash window is closed -- release the
-- crash breaker (a payload ERROR is visible in the log/ack, not a crash)
pcall(os.remove, "data/aai_attach_inflight.txt");
if first_ok then
	pcall(os.remove, "data/aai_attach_tripped.txt");
end;

-- ---- 7. the pump timer + reload supervisor ----------------------------
local json = nil;
pcall(function() json = require "aai_json"; end);

local RELOAD = "data/aai_reload.txt";
local RELOAD_ACK = "data/aai_reload_ack.json";

local function read_reload_seq()
	local fh = io.open(RELOAD, "r");
	if not fh then
		return nil;
	end;
	local l1 = fh:read("*l") or "";
	fh:close();
	return tonumber(string.match(l1, "^seq%s+(%d+)"));
end;

-- pre-consume whatever seq is already on disk: it predates this battle
local last_reload = read_reload_seq() or 0;

local no_timer = io.open("data/aai_no_ctimer.txt", "r");
if no_timer then
	no_timer:close();
	w("  pump timer SKIPPED (data/aai_no_ctimer.txt) -- feed + reload dead");
else
	local tick_n = 0;
	rawset(_G, "aai_custom_tick", function()
		-- runs INSIDE the engine's timer dispatch: an uncaught error here
		-- is a crash, so the whole body is pcall'd
		pcall(function()
			tick_n = tick_n + 1;
			rawset(_G, "aai_tick_count", tick_n);
			if tick_n <= 3 or tick_n % 600 == 0 then
				w("  custom tick " .. tick_n .. " clock=" .. tostring(os.clock()));
			end;
			-- reload supervisor: ~1s cadence, instead of the kick that tick
			if tick_n % 10 == 0 then
				local seq = read_reload_seq();
				if seq and seq > last_reload then
					last_reload = seq;
					w("  RELOAD seq " .. seq .. " at tick " .. tick_n);
					payload_teardown();
					local ok, err = payload_load("reload " .. seq);
					if json then
						pcall(json.write, RELOAD_ACK, {
							seq = seq, ok = ok and true or false,
							err = (not ok) and tostring(err) or nil,
							tick = tick_n, clock = os.clock(),
						});
					end;
					return;
				end;
			end;
			-- 2s warm-up keeps the first pumps out of the load window;
			-- publish's pump_core rate-limits 100ms kicks to ~500ms
			if tick_n >= 20 then
				local k = rawget(_G, "aai_pub_kick");
				if k then
					k("ctimer");
				end;
			end;
		end);
	end);
	local okt, errt = pcall(function()
		battle:register_repeating_timer("aai_custom_tick", 100);
	end);
	w("  register_repeating_timer(aai_custom_tick, 100) -> " ..
		(okt and "OK" or ("ERR " .. tostring(errt))));

	-- TIMER-DISPATCH BISECT (2026-08-05): the Plains land battle serviced
	-- ZERO ticks all battle while yesterday's coastal battle ticked 10k+.
	-- Lever data/aai_ss_probe.txt adds a singleshot registration to split
	-- "dispatch dead" from "repeating broken". DEFAULT OFF and keep it off
	-- in working battles: a SECOND registration is the campaign kill
	-- pattern (timer law) and would contaminate a clean A/B.
	local ss = io.open("data/aai_ss_probe.txt", "r");
	if ss then
		ss:close();
		rawset(_G, "aai_single_probe", function()
			pcall(function()
				w("  SINGLESHOT fired clock=" .. tostring(os.clock()));
			end);
		end);
		local oks, errs = pcall(function()
			battle:register_singleshot_timer("aai_single_probe", 1500);
		end);
		w("  register_singleshot_timer(aai_single_probe, 1500) -> " ..
			(oks and "OK" or ("ERR " .. tostring(errs))));
	end;
end;

rawset(_G, "aai_stack_up", true);
w("==== RUN 6 kernel up (payload " .. (first_ok and "OK" or "FAILED -- fix + reload")
	.. ") ====");
