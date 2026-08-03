-------------------------------------------------------------------------
--	ml/game/write/surface.lua -- THE write API (action translation layer).
--
--	Takes decoded model actions in exact wire form and translates each
--	(verb, args) into engine calls via the providers -- the mirror image
--	of the read surface. PURE policy-free translation: the pump owns
--	when apply() AND grip_tick() run (conflict phase only -- deployment
--	engine-touch froze the dispatch on probe steps; writes untested
--	there, conflict-only by policy) and the ack goes back through IPC.
--
--	Verb-level translation rules baked in from ML_DESIGN:
--	- set_stance maps per stance key: fire_at_will() / melee() /
--	  change_behaviour_active(name, on).
--	- enter_structure picks climb vs defend by structure class.
--	- Index args arrive 0-based (codec converts once); vocab indices
--	  (shot/ability/formation) resolve to engine keys via the T2 rosters.
--
--	Control-ownership laws (the grip):
--	- take_control() is NOT durable: re-assert per tick, for every
--	  gripped unit (grip_tick(), called by the pump each decision tick).
--	- A bare take does NOT cancel the in-flight AI order: first grip of
--	  a unit is take + halt.
--	- Rout suspends script control; rally hands the unit to the AI:
--	  skip routing/shattered, re-take + re-issue on recovery.
--	- Drift watchdog: poll ordered_position(), re-impose ONLY on
--	  divergence -- never re-spam goto (resets pathing).
-------------------------------------------------------------------------

local spec = require "ml/spec";
local util = require "ml/util";
local roster = require "ml/roster";

local M = {};

local ML = nil;
local plua = nil;
local pdb = nil;

function M.init(core)
	ML = core;
	plua = require "ml/providers/lua";
	pdb = require "ml/providers/db";
end;

-- Apply one act frame: actions = list of
-- { u, verb, x, y, deg, width_m, run, enemy, struct, vehicle, shot,
--   stance, on, formation, ability } (only the verb's args present).
-- Returns per-action results [{u, verb, ok, err}] for the ack ring.
-- OK means DISPATCHED, never effect (silent-accept law) -- effect shows
-- up in the next observation's order-state block.
function M.apply(actions)
	return nil, "todo(per action: roster lookup, skip routing/shattered, grip, dispatch M[verb]; pcall each; collect results)";
end;

-- Per-verb translation (slot = roster position; targets pre-resolved by
-- apply from wire indices to roster/scan entries).

function M.move(slot, x, y, run)
	return false, "todo(T1 api.move -> uc:goto_location)";
end;

function M.move_formation(slot, x, y, deg, width_m, run)
	return false, "todo(T1 api.move_form -> uc:goto_location_angle_width)";
end;

function M.halt(slot)
	return false, "todo(T1 api.halt)";
end;

function M.attack_unit(slot, enemy_slot, run)
	return false, "todo(T1 api.attack_unit; enemy roster entry -> unit userdata)";
end;

function M.attack_ground(slot, x, y)
	return false, "todo(T1 api.attack_ground -> uc:attack_location)";
end;

function M.attack_building(slot, struct_slot)
	return false, "todo(T1 api.attack_building -- unverified; struct scan entry -> building handle)";
end;

function M.change_shot_type(slot, shot_idx)
	return false, "todo(T1 api.shot_type; shot_idx -> projectile key via pdb.shots roster)";
end;

function M.set_stance(slot, stance_idx, on)
	return false, "todo(per spec.STANCES[stance_idx+1]: fire_at_will/melee special-cased, rest api.behaviour(name, on))";
end;

function M.set_formation(slot, formation_idx)
	return false, "todo(T1-attempt api.ability('form_*') -- no uc setter exists; T4 native fallback seed)";
end;

function M.use_unit_ability(slot, ability_idx)
	return false, "todo(T1-attempt api.ability(key); ability_idx -> key via pdb.abilities roster)";
end;

function M.use_general_ability(slot, ability_idx)
	return false, "todo(T1-attempt api.ability(general key); general unit only)";
end;

function M.enter_structure(slot, struct_slot)
	return false, "todo(class-switch: wall/tower -> api.climb; garrisonable -> api.defend -- defend unverified)";
end;

function M.leave_building(slot)
	return false, "todo(T1 api.leave -- unverified)";
end;

function M.occupy_vehicle(slot, vehicle_slot)
	return false, "todo(T1 api.occupy_vehicle -- unverified verb family)";
end;

-- The grip: per-tick control re-assertion over every slot the model owns.
function M.grip_tick()
	return nil, "todo(re-take per gripped unit; skip routing/shattered; re-grip + re-issue on rally; drift watchdog via ordered_position)";
end;

function M.release_all()
	return nil, "todo(release every gripped uc -- battle end / disarm path)";
end;

return M;
