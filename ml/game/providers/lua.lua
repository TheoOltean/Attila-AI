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
	if obj == nil then return nil; end;
	local ok, fn = pcall(function() return obj[method]; end);
	if not ok or type(fn) ~= "function" then return nil; end;
	local ok2, r = pcall(fn, obj, ...);
	if not ok2 then return nil; end;
	if type(r) == "function" then
		local ok3, r2 = pcall(r);
		if not ok3 then return nil; end;
		return r2;
	end;
	return r;
end;

-- List :count() coerced to a number, 0 when the list is pre-population.
function M.count(list)
	local n = M.read(list, "count");
	if type(n) ~= "number" then return 0; end;
	return n;
end;

-- Position userdata -> x, y(elev), z triple via get_x/get_y/get_z.
function M.vec(pos)
	local x = M.read(pos, "get_x");
	local y = M.read(pos, "get_y");
	local z = M.read(pos, "get_z");
	if type(x) == "number" and type(y) == "number" and type(z) == "number" then
		return x, y, z;
	end;
	return nil;
end;

-- Entry-env closure dispatch for writes: call(closure_name, uc, ...).
function M.call(name, ...)
	local fn = ML and ML.api and ML.api[name];
	if type(fn) ~= "function" then
		return false, "no api closure: " .. tostring(name);
	end;
	local ok, r = pcall(fn, ...);
	if not ok then return false, tostring(r); end;
	return true, r;
end;

return M;
