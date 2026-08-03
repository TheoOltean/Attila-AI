-------------------------------------------------------------------------
--	ml/game/ipc/pump.lua -- THE driver: the only place policy lives.
--
--	Entry: kick() from boot's vanilla-tick wrapper (100 ms, engine-legal
--	context, from tick >= 50). Everything downstream of kick() runs
--	pcall'd. The battle SIM clock pauses with the game, so the whole
--	pipeline freezes on pause -- correct behavior, host tolerates a
--	stale feed by design.
--
--	Per kick:
--	1. rate-gate 100 ms kicks down to the 1 s decision tick
--	   (floor 0.9x -- admitting fast pumps inflates the tick clock);
--	2. once, when ready: hello frame (channel.reset is OWNED by init --
--	   exactly one anti-replay reset point per battle);
--	3. CONFLICT PHASE ONLY -- both write paths sit behind ONE gate:
--	   act inbox (poll -> parse -> write.apply; non-conflict frames are
--	   ERR-acked and consumed, never queued) AND write.grip_tick()
--	   (the grip issues engine writes too: take/halt/re-issue).
--	   On leaving conflict: write.release_all(), grip set cleared.
--	   Why conflict-only: deployment engine-touch froze the dispatch
--	   (measured on probe steps; writes untested there -- conservative
--	   policy, not a measured write-kill);
--	4. read.observation() -> encode -> channel.write(obs)
--	   (skip the write on a transitional nil frame -- last-good stays).
--	Phase tracking: events only flip plain-Lua flags (context law);
--	this pump reads the flags.
-------------------------------------------------------------------------

local spec = require "ml/spec";
local util = require "ml/util";
local channel = require "ml/ipc/channel";
local codec = require "ml/ipc/codec";
local read = require "ml/read/surface";
local write = require "ml/write/surface";

local M = {};

local ML = nil;
local decision_tick = 0;	-- small monotonic int, the obs seq AND tick
local said_hello = false;

function M.init(core)
	ML = core;
	-- todo: register phase-flag event handlers (flag flips ONLY);
	-- channel.reset(ML.battle_id) -- SOLE owner of the battle-start reset
end;

-- The 100 ms entry. Cheap early-outs first; body guarded.
function M.kick()
	return nil, "todo(rate-gate to DECISION_TICK_S; hello_once(); if conflict then pump_in(); write.grip_tick() elseif just_left_conflict then write.release_all() end; pump_out())";
end;

-- Steps (each pcall'd by kick; a failing step logs and skips the tick,
-- it never kills the pump).

function M.hello_once()
	return nil, "todo(read.ready() gate; codec.encode_hello(battle_id, read.map_info()) -> channel.write)";
end;

function M.pump_in()
	return nil, "todo(channel.poll_act -> codec.parse_act -> phase gate -> write.apply -> ack ring append via codec.encode_ack)";
end;

function M.pump_out()
	return nil, "todo(decision_tick++; read.observation(decision_tick) -> codec.encode_obs (header: applied seq + errs) -> channel.write; nil obs = skip, keep last-good)";
end;

return M;
