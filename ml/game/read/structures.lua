-------------------------------------------------------------------------
--	ml/game/read/structures.lua -- structures[512][16] assembler.
--
--	BUILDINGS LAW (the hard one): the registry has live, REVERSIBLE
--	warm/cold state; touching a cold entry kills the timer dispatch with
--	zero Lua error, entries revert mid-battle, and NO timing rule is
--	safe ("warm at scan time" included). Only the blind unguarded walk
--	(raw pcalls, NO guards, NO cold detection, NO function-unwrap --
--	a function return is REJECTED, never called) is production-verified,
--	and a first scan on an unfamiliar map is a throwaway.
--
--	Design consequence: the registry is touched EXACTLY ONCE per battle
--	(the scan). There is NO per-tick registry read of any kind. Live
--	state after the scan comes from engine EVENTS (BattleUnitAttacks/
--	Destroys/CapturesBuilding etc. -- handlers flip plain-Lua flags per
--	the context law; the pump folds them into the index) and, for
--	anything events can't carry (health floats, owner), T3 native reads
--	on handles captured at scan time -- never the Lua registry again.
-------------------------------------------------------------------------

local spec = require "ml/spec";
local util = require "ml/util";

local M = {};

function M.init(core)
end;

function M.reset()
	return nil, "todo(clear the per-battle structure index + event state)";
end;

-- ONCE per battle: the blind walk -> tactical-structure index
-- (slot -> {handle, class, x, y, health0}); capped at MAX_STRUCT.
-- Raw pcalls only; results memoized; NEVER re-run mid-battle.
function M.scan()
	return nil, "todo(T1 bld-v1 blind walk: item(i)/name()/central_position()/health(); classify per spec.STRUCT_CLASSES; throwaway-battle rule applies)";
end;

-- Event-flag drain, called by the pump: fold flipped building-event
-- flags (attacked/destroyed/captured/fire) into the index. Plain Lua.
function M.fold_events()
	return nil, "todo(consume event flags set by boot's handlers; update index rows)";
end;

-- Per tick: [512][16] rows assembled from the INDEX ONLY (scan statics
-- + event-folded state + T3 native reads where wired). Zero registry
-- touches -- this function must never call an engine method on a
-- registry entry.
function M.build()
	return nil, "todo(rows from index + event state; health/owner refresh = T3 native on scan-time handles when wired)";
end;

return M;
