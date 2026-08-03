-------------------------------------------------------------------------
--	ml/game/providers/lua.lua -- T1: the live engine Lua binding.
--
--	Reads are method calls on engine userdata, made from pump context on
--	objects reached via _G.aai_ml.battle; writes go through the entry-env
--	closures in _G.aai_ml.api. Every touch is pcall-guarded: one bad
--	getter must never kill a snapshot (error law), and a method field can
--	yield a bound-getter FUNCTION that needs unwrapping (engine quirk).
--
--	SCOPE RESTRICTION: M.read is for unit/army/alliance/battle userdata
--	ONLY -- NEVER for buildings-registry entries. On the registry a
--	function return is the COLD signature and force-calling it is part
--	of the guarded pattern that kept killing the dispatch; the blind
--	walk in read/structures.lua uses its own raw pcalls and REJECTS
--	function returns (buildings law).
-------------------------------------------------------------------------

local M = {};

local ML = nil;

function M.init(core)
	ML = core;
end;

function M.ready()
	return ML ~= nil and ML.battle ~= nil;
end;

-- The guarded read primitive: pcall unit:method(...), unwrap a returned
-- bound-getter function, nil on any failure. (v1-proven, reimplemented.)
function M.read(obj, method, ...)
	return nil, "todo(pcall obj[method](obj, ...); if function returned, call it; nil on failure)";
end;

-- List :count() coerced to a number, 0 when the list is pre-population.
function M.count(list)
	return 0, "todo(pcall list:count(); coerce non-number to 0)";
end;

-- Position userdata -> x, y(elev), z triple via get_x/get_y/get_z.
function M.vec(pos)
	return nil, "todo(pcall get_x/get_y/get_z)";
end;

-- Entry-env closure dispatch for writes: call(closure_name, uc, ...).
function M.call(name, ...)
	return false, "todo(route to ML.api[name], pcall'd; false,err when missing)";
end;

return M;
