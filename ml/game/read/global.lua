-------------------------------------------------------------------------
--	ml/game/read/global.lua -- the global[40] assembler.
--
--	Layout (spec.GLOBAL + ML_DESIGN.md): time elapsed/remaining (/1800),
--	attacker bit, battle-type one-hot [3,9), weather one-hot [9,13),
--	men+unit counts [13,19), victory points [19,34), spare [34,40).
--	Clock source law: remaining_conflict_time() is the authoritative
--	battle clock (tick-derived time drifts under speed changes).
-------------------------------------------------------------------------

local spec = require "ml/spec";
local util = require "ml/util";

local M = {};

function M.init(core)
end;

-- Full vector (Lua array of 40 numbers; zeros where a field is not yet
-- readable -- each gap below carries its provider seed).
function M.build()
	local v = util.zeros(spec.GLOBAL_DIM);
	-- todo idx 0/1: both from the authoritative clock -- remaining =
	-- remaining_conflict_time(); elapsed = initial_remaining - remaining
	-- (never the tick clock: it drifts under battle speed changes)
	-- todo idx 2: attacker flag (T1 battle setup; source to pin down)
	-- todo [3,9): battle-type one-hot (taxonomy [G]; T1/T3 source open)
	-- todo [9,13): weather one-hot (T3 -- not yet readable)
	-- todo [13,19): men + unit counts (T1 walk: number_of_men_alive etc.)
	-- todo [19,34): victory points (T5 -- events never deliver, Lua getters nil)
	return v;
end;

return M;
