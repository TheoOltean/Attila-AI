-------------------------------------------------------------------------
--	ml/game/read/terrain.lua -- map identity + terrain raster policy.
--
--	The rasters (terrain[11][128][128], terrain_hires[6][2048][2048]) are
--	NEVER shipped per tick and mostly cannot be read live yet (T5 bulk
--	geometry: heightmap/walkable/navmesh are not-yet). Policy: rasters
--	are host-side per-map assets keyed by map identity; the game side
--	contributes (a) map_info for the hello frame and (b) dynamic raster
--	FACTS as small aux entries (breach/gate-open events -> hires ch 3
--	updates; enemy centroid -> ch 10 blob), painted host-side.
--	Point elevation sampling (T1 vector:get_y()) exists for spot checks
--	and offline map capture, not the per-tick path.
-------------------------------------------------------------------------

local M = {};

function M.init(core)
end;

-- { key, w, h, min_z } for the hello frame; MAP_W/MAP_H are the position
-- normalization divisors, min_z the elevation zero point.
function M.map_info()
	return nil, "todo(map key source; bounds/dims source; min_z from offline capture or T5)";
end;

-- Point sample for offline capture tooling (NOT the per-tick path).
function M.elevation_at(x, z)
	return nil, "todo(T1 battle_vector set + get_y())";
end;

-- Dynamic raster facts since last tick (breach/gate events for hires
-- ch 3; flags flipped by events, read here from pump context).
function M.raster_events()
	return nil, "todo(event-flag drain: BattleUnitDestroysBuilding etc. -> {kind, x, y} list)";
end;

return M;
