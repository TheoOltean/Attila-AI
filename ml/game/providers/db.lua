-------------------------------------------------------------------------
--	ml/game/providers/db.lua -- T2: the static DB card, joined on type().
--
--	Source of the [0:44] static-card block and the owned formation /
--	ability / shot-type rosters. The extract (1145 land types) lives
--	host-side as a training asset; the game-side provider only needs the
--	per-type values the feature builder folds into feat vectors. HOW the
--	card reaches this Lua state is an open wiring decision (options in
--	ml/README.md: pack-shipped generated lua table vs host echo in the
--	act stream vs host-side join). Until decided, every accessor is a
--	stub and read/units.lua treats the static block as todo.
-------------------------------------------------------------------------

local M = {};

function M.init(core)
end;

-- Normalized static card for a unit type key: array of 44 numbers
-- (spec.FEAT.STATIC layout, divisors from spec.NORM).
function M.card(type_key)
	return nil, "todo(T2 join the DB land-unit extract (reference/DB_DATA.md) by type via the delivery mechanism above -- never a live read of v1's runtime json; normalize per spec.NORM)";
end;

-- Owned rosters for a type (drive owned multi-hots + write translation):
-- formations: array of form_* keys (<= 2); abilities: array of ability
-- keys (<= 3 + general); shots: array of projectile keys (<= 4).
function M.formations(type_key)
	return nil, "todo(T2 land_units_to_unit_abilites_junctions form_* keys)";
end;

function M.abilities(type_key)
	return nil, "todo(T2 junction non-form_* keys + general roster via requires_effect_enabling)";
end;

function M.shots(type_key)
	return nil, "todo(T2 projectile shot_types roster)";
end;

return M;
