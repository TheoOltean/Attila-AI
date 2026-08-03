-------------------------------------------------------------------------
--	ml/game/read/vehicles.lua -- vehicles[32][12] assembler.
--
--	Siege equipment (rams, towers, ladders, deployables). T1 surface:
--	assault_equipment():vehicle_count() + vehicle_item(i):position() --
--	position only; type/crew/owner classification is T3 territory
--	(vehicle struct exposes nothing else via Lua).
-------------------------------------------------------------------------

local spec = require "ml/spec";
local util = require "ml/util";

local M = {};

function M.init(core)
end;

function M.reset()
	return nil, "todo(clear per-battle vehicle slot index)";
end;

-- Per tick: [32][12] rows, stable slots per vehicle item.
function M.build()
	return nil, "todo(T1 vehicle_count/vehicle_item(i):position(); type one-hot + is_crewed + owner = T3 vehicle struct reads)";
end;

return M;
