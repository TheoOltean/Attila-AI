-------------------------------------------------------------------------
--	ml/game/roster.lua -- stable slot assignment, shared by BOTH surfaces.
--
--	The load-bearing invariant (v1-proven): every unit carries a stable
--	identity key "alliance:army:name" (unit:name() is the per-army spawn
--	ordinal, unique and append-only as reinforcements arrive). The slot a
--	unit gets here is the SAME slot the host sees in obs arrays and the
--	same `u` index act frames target -- the join between read and write.
--
--	Slot law: positions 1..MAX (Lua-natural); assigned in first-seen
--	order, append-only within a battle, NEVER reused or compacted (a dead
--	unit keeps its slot with exists=0). Wire index = position - 1,
--	converted only in ipc/codec.
-------------------------------------------------------------------------

local spec = require "ml/spec";

local M = {};

-- side = "friendly" | "enemy" (friendly = the controlled side).
local state = { friendly = {}, enemy = {}, by_key = {} };

-- Wipe all slot tables. Call once per battle, from boot, before first use.
function M.reset(battle_id)
	state = { friendly = {}, enemy = {}, by_key = {} };
	M.battle_id = battle_id;
end;

-- Assign (or return the existing) slot position for a unit.
--	key: "alliance:army:name" identity string
--	unit: engine unit userdata - uc: unit_controller (friendly side only)
-- Returns position 1..MAX, or nil, "roster-full" past the slot cap.
function M.assign(side, key, unit, uc)
	return nil, "todo(first-seen append; cap MAX_FRIENDLY/MAX_ENEMY; store {key, unit, uc, first_tick})";
end;

-- Slot position for a known key, or nil if never seen.
function M.slot_of(key)
	return state.by_key[key];
end;

-- Entry table {key, unit, uc} at a position, or nil for an empty slot.
function M.entry(side, pos)
	return state[side] and state[side][pos] or nil;
end;

-- Highest assigned position for a side (iterate 1..count; holes impossible).
function M.count(side)
	return state[side] and #state[side] or 0;
end;

-- Walk live engine units and assign slots for any not yet seen
-- (reinforcements append). Engine-touching: pump context only.
function M.sync()
	return nil, "todo(T1 walk battle->alliances->armies->units; reinforcements via army:get_reinforcement_units())";
end;

return M;
