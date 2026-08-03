-------------------------------------------------------------------------
--	ml/game/read/units.lua -- unit-token assembler: feat[40][224] + types.
--
--	Same 224 layout both sides (spec.FEAT blocks). Per-block routing:
--	  [0:44]   static card      T2 db.card(type)
--	  [44:70]  live state       T1 methods; morale/fatigue/frontage T3
--	  [70:84]  order state      T1 ordered_position + T3 order_state
--	  [84:95]  stance bits      T1 is_behaviour_active(name)
--	  [95:115] formation        owned T2; current T3 (no T1 read)
--	  [115:211] abilities       owned T2; ready-now T3 ONLY (see below)
--	  [211:224] meta            T1/roster
--	Enemy fill policy (spec, iterating): order block zeroed, ready-now
--	zeroed, hidden units keep last-seen x/y with visible_now = 0 and
--	last_seen_age growing -- the last-seen memory lives HERE (this module
--	is stateful per battle; reset() wipes it).
-------------------------------------------------------------------------

local spec = require "ml/spec";
local util = require "ml/util";
local roster = require "ml/roster";

local M = {};

function M.init(core)
end;

-- Wipe per-battle state (enemy last-seen memory, static-card cache).
function M.reset()
	return nil, "todo(clear last_seen + card cache)";
end;

-- side = "friendly" | "enemy" ->
-- { types = [MAX type-key strings or false per slot], feat = [MAX][224] }.
function M.build(side)
	return nil, "todo(walk roster slots; static(), live(), order_state(), stances(), formation(), abilities(), meta() per unit; apply enemy fill policy)";
end;

-- Block assemblers (each returns its slice; stubs carry routing seeds).

function M.static(entry)
	return nil, "todo(T2 db.card(unit:type()); cached per type per battle)";
end;

function M.live(entry)
	return nil, "todo(T1 position/bearing/men/ammo/flags; T3 morale(missing offset)/fatigue(0x1cb4)/frontage; elevation norm (z - min_z)/100)";
end;

function M.order_state(entry)
	return nil, "todo(T1 ordered_position() + command-event flags; T3 order_state 0x1b5c for order type; order-age counter kept here)";
end;

function M.stances(entry)
	return nil, "todo(T1 is_behaviour_active per spec.STANCES key; defend/formed_attack/unlimber unverified)";
end;

function M.formation(entry)
	return nil, "todo(owned: T2 db.formations; current: T3 ability-state read -- no T1 exists)";
end;

-- LAW: the ready-now bits are T3 native (ability-state struct read) and
-- NOTHING else. The T1 arg-variant can_perform_special_ability(name)
-- polled from a feed is CONVICTED LETHAL (froze three battles at the
-- first command event -- SCRIPTING.md); never wire it here. Until the
-- T3 read lands, ready-now stays all-zero.
function M.abilities(entry)
	return nil, "todo(owned: T2 db.abilities -> 48-vocab bits; ready: T3 native ability-state read -- all-zero until the offset lands)";
end;

function M.meta(entry)
	return nil, "todo(is_own_side/army index/reinforcement/arrived from roster + T1)";
end;

return M;
