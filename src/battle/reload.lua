-------------------------------------------------------------------------
--	MID-BATTLE HOT RELOAD, as a module riding the publish pump.
--
--	Why here instead of the attach chunk: run 6's kernel supervisor has
--	never once executed -- it rides a timer dispatch that run 6 itself
--	kills (reference/CUSTOM_BATTLES.md). Run-5 mode ticks indefinitely, so
--	the supervisor moves into the module stack, where it reaches the SAME
--	legal context by a working route: publish's pump runs inside the engine
--	timer callback, and that is where engine calls are allowed at all
--	(SCRIPTING.md context law). Nothing here runs at battle load except
--	registration -- the load window is exactly what run 6 poisons.
--
--	Cycle: the cockpit's /reload copies src/ -> data/aai_dev/ and bumps
--	data/aai_reload.txt ("seq N"). We see the new seq on a pump tick, tear
--	the stack down (event handlers truncated to the snapshot taken at our
--	init, pump riders cleared, module family dropped from package.loaded),
--	re-execute every module from data/aai_dev/ with stdio + loadstring,
--	re-init them, and ack to data/aai_reload_ack.json.
--
--	MUST BE FIRST in battle/modules.lua: the event snapshot has to be taken
--	before any other module registers a handler, or teardown cannot remove
--	what they added. This module is NOT itself reloadable -- it is the
--	floor being stood on -- so edits to THIS file need a pack rebuild.
-------------------------------------------------------------------------

local M = {};

local RELOAD = "data/aai_reload.txt";
local ACK = "data/aai_reload_ack.json";

--	deps first, then the modules that get init(core) -- same order the
--	kernel pre-stuffs, so a require inside any of them hits the cache
local DEPS = { "aai_json", "battle/db_abilities", "battle/native", "battle/db" };
local STACK = { "battle/api", "battle/publish", "battle/probe", "battle/harness" };

local core = nil;
local ev_snap = {};
local last_seq = 0;
local reloads = 0;
local ticks = 0;

local function dev_path(name)
	return "data/aai_dev/" .. string.gsub(name, "%.", "/") .. ".lua";
end;

local function read_seq()
	local fh = io.open(RELOAD, "r");
	if not fh then
		return nil;
	end;
	local l1 = fh:read("*l") or "";
	fh:close();
	return tonumber(string.match(l1, "^seq%s+(%d+)"));
end;

--	compiled chunk of a dev copy, or nil (absent or broken -- a broken edit
--	must leave the running stack alone, not half-replace it).
--
--	2026-08-06: reload #1 silently ran the PACK copy -- io.open returned nil
--	and the caller could not tell "no dev copy" from "could not read it", so
--	the fallback looked like success. Both the error string and a loadfile
--	second attempt are reported now; `src` in the returned trace says which
--	route actually produced the chunk.
local function dev_chunk(name)
	local path = dev_path(name);
	local fh, oerr = io.open(path, "r");
	if fh then
		local src = fh:read("*a");
		fh:close();
		local chunk, cerr = loadstring(src, "@" .. path);
		if not chunk then
			return nil, cerr, "broken";
		end;
		return chunk, nil, "dev(" .. tostring(string.len(src)) .. "b)";
	end;
	-- io.open refused: say why, then try the other stdio route before
	-- falling back to the pack
	local lf, lerr = loadfile(path);
	if lf then
		return lf, nil, "dev-loadfile";
	end;
	return nil, nil, "pack(io:" .. tostring(oerr) .. " loadfile:"
		.. tostring(lerr) .. ")";
end;

local function teardown()
	-- every handler added after our init disappears; ours and the engine's
	-- pre-existing ones survive
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
	rawset(_G, "aai_pub_kick", nil);
	rawset(_G, "aai_probe_tick", nil);
	rawset(_G, "aai_harness_tick", nil);
	pcall(function()
		for _, name in ipairs(DEPS) do
			package.loaded[name] = nil;
		end;
		for _, name in ipairs(STACK) do
			package.loaded[name] = nil;
		end;
	end);
end;

--	returns ok, err -- a failure here leaves the battle without a feed, so
--	the ack (and the cockpit) must show it loudly
local function bring_up()
	local trace = {};
	local from_pack = 0;
	local function note(name, src)
		local slug = string.match(name, "([^/]+)$") or name;
		trace[#trace + 1] = slug .. "=" .. src;
		if string.sub(src, 1, 4) == "pack" then
			from_pack = from_pack + 1;
		end;
	end;
	for _, name in ipairs(DEPS) do
		local chunk, cerr, src = dev_chunk(name);
		note(name, src);
		if cerr then
			return false, name .. ": " .. tostring(cerr), trace;
		end;
		if chunk then
			local ok, mod = pcall(chunk);
			if not ok then
				return false, name .. ": " .. tostring(mod), trace;
			end;
			package.loaded[name] = (mod == nil) and true or mod;
		end;
	end;
	for _, name in ipairs(STACK) do
		local mod;
		local chunk, cerr, src = dev_chunk(name);
		note(name, src);
		if cerr then
			return false, name .. ": " .. tostring(cerr), trace;
		end;
		if chunk then
			local ok, got = pcall(chunk);
			if not ok then
				return false, name .. ": " .. tostring(got), trace;
			end;
			mod = got;
			package.loaded[name] = (mod == nil) and true or mod;
		else
			local ok, got = pcall(require, name);
			if not ok then
				return false, name .. ": " .. tostring(got), trace;
			end;
			mod = got;
		end;
		if type(mod) == "table" and type(mod.init) == "function" then
			local ok, err = pcall(mod.init, core);
			if not ok then
				return false, name .. ".init: " .. tostring(err), trace;
			end;
		end;
	end;
	return true, (from_pack > 0)
		and (tostring(from_pack) .. " module(s) fell back to the PACK -- edits did NOT land")
		or nil, trace;
end;

local function ack(seq, ok, err)
	local sent = pcall(function()
		local json = require "aai_json";
		json.write(ACK, {
			seq = seq, ok = ok and true or false,
			err = (not ok) and tostring(err) or nil,
			reloads = reloads, clock = os.clock(),
		});
	end);
	if not sent then
		local fh = io.open(ACK, "w");
		if fh then
			fh:write('{"seq":' .. tostring(seq) .. ',"ok":'
				.. (ok and "true" or "false") .. '}\n');
			fh:close();
		end;
	end;
end;

--	rides the pump LAST (publish calls us after probe + harness), so the
--	stack we are about to replace has already done its work this tick
local function tick()
	ticks = ticks + 1;
	if ticks % 10 ~= 0 then		-- ~1s at the 100ms pump
		return;
	end;
	local seq = read_seq();
	if not seq or seq <= last_seq then
		return;
	end;
	last_seq = seq;
	reloads = reloads + 1;
	core.log("RELOAD seq " .. seq .. " -- tearing the stack down");
	teardown();
	local ok, err, trace = bring_up();
	-- ALWAYS say where each module came from: reload #1 (2026-08-06) reported
	-- OK while every module had quietly come from the pack, so "OK" alone is
	-- not evidence that an edit landed.
	core.log("RELOAD seq " .. seq .. " sources: "
		.. table.concat(trace or {}, " "));
	if ok and err then			-- ok, but some module fell back
		core.log("RELOAD seq " .. seq .. " WARNING: " .. tostring(err));
	elseif ok then
		core.log("RELOAD seq " .. seq .. " OK (reload #" .. reloads
			.. ") -- every module from data/aai_dev/");
	else
		core.log("RELOAD seq " .. seq .. " FAILED: " .. tostring(err)
			.. " -- fix the file and reload again; the feed is down until then");
	end;
	ack(seq, ok, err);
end;

function M.init(c)
	core = c;
	-- phase truth for reloaded modules: they seed from _G.aai_phase at init,
	-- and the phase events fired long before any mid-battle reload. Handlers
	-- set a plain Lua flag ONLY -- engine calls from an events context are
	-- forbidden (SCRIPTING.md). Registered BEFORE the snapshot so teardown
	-- keeps them.
	if rawget(_G, "aai_phase") == nil then
		rawset(_G, "aai_phase", "loading");
	end;
	pcall(function()
		local ev = rawget(_G, "events");
		if type(ev) ~= "table" then
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
	end);

	-- snapshot AFTER our own handlers, BEFORE the rest of the stack loads
	pcall(function()
		local ev = rawget(_G, "events");
		if type(ev) ~= "table" then
			return;
		end;
		for name, t in pairs(ev) do
			if type(t) == "table" then
				ev_snap[name] = #t;
			end;
		end;
	end);

	-- pre-consume the seq on disk: it belongs to a previous battle
	last_seq = read_seq() or 0;
	rawset(_G, "aai_reload_tick", core.guarded("reload supervisor", tick));
	core.log("reload: supervisor armed (pump rider, seq " .. last_seq
		.. ") -- cockpit sync + reload replaces the stack from data/aai_dev/");
end;

return M;
