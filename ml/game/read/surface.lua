-------------------------------------------------------------------------
--	ml/game/read/surface.lua -- THE read API.
--
--	One clean surface over the tiered providers (T1 live Lua, T2 DB,
--	T3 native, T5 geometry). Callers get spec-pure components -- e.g.
--	global_state() IS the 40-dim vector -- and never see which provider
--	filled which field. PURE module: no timers, no file IO, no phase
--	policy; the pump owns when this runs (engine-legal context) and what
--	happens to the result.
--
--	Component -> assembler routing:
--	  global[40]        read/global.lua
--	  friendly / enemy  read/units.lua   (feat[40][224] + type keys)
--	  structures        read/structures.lua
--	  vehicles          read/vehicles.lua
--	  terrain rasters   read/terrain.lua (T5; host-side assets until then)
--	  aux facts         here (host feature-builder inputs, e.g. centroid)
--	Masks are NOT built here: legal(obs) is host-side (host/masks.py),
--	one pure versioned function shared verbatim with training.
-------------------------------------------------------------------------

local spec = require "ml/spec";
local roster = require "ml/roster";
local plua = require "ml/providers/lua";
local g = require "ml/read/global";
local units = require "ml/read/units";
local structures = require "ml/read/structures";
local vehicles = require "ml/read/vehicles";
local terrain = require "ml/read/terrain";

local M = {};

local ML = nil;

function M.init(core)
	ML = core;
	-- Providers are initialized by boot (their own layer step, shared with
	-- the write surface) -- this surface only wires its assemblers.
	g.init(core);
	units.init(core);
	structures.init(core);
	vehicles.init(core);
	terrain.init(core);
end;

-- True when the battle interface is live (transitional/teardown ticks
-- read nil everywhere -- callers skip the frame, keep last-good on disk).
function M.ready()
	if not (ML and ML.battle) then return false; end;
	return type(plua.read(ML.battle, "local_alliance")) == "number";
end;

-- One complete observation at decision tick `tick`:
-- { global, friendly = {types, feat}, enemy = {types, feat},
--   structures, vehicles, aux }. Types are engine type-key strings;
-- the host codec maps key -> vocab id (the id table is a training asset).
-- Grows with the assemblers: a component whose assembler is still a stub
-- returns nil and is simply absent from the frame (global is the first
-- live one). roster.sync joins in when the unit assembler lands.
function M.observation(tick)
	if not M.ready() then return nil; end;
	local obs = {};
	obs.global = g.build();
	obs.friendly = units.build("friendly");
	obs.enemy = units.build("enemy");
	obs.structures = structures.build();
	obs.vehicles = vehicles.build();
	obs.aux = M.aux();
	return obs;
end;

-- global[40] (spec-exact, normalized).
function M.global_state()
	return g.build();
end;

-- Controlled-side tokens: { types = [40 keys], feat = [40][224] }.
function M.friendly()
	return units.build("friendly");
end;

-- Enemy tokens, same layout; fill policy (visible_now / last_seen_age /
-- zeroed order block) lives in units.lua.
function M.enemy()
	return units.build("enemy");
end;

-- structures[512][16].
function M.structures()
	return structures.build();
end;

-- vehicles[32][12].
function M.vehicles()
	return vehicles.build();
end;

-- Per-battle statics for the hello frame: map identity + dims + min z.
function M.map_info()
	return terrain.map_info();
end;

-- Small raw facts the host feature builder finishes into rasters/features
-- (e.g. TRUE enemy-army centroid incl. hidden -> terrain ch 10 blob).
function M.aux()
	return nil, "todo(enemy centroid true-mass; VP live state when readable)";
end;

return M;
